"""
Field-level transformations implementing STTM-001.

Every function here is pure and returns a Decision, not a bare value. That shape
is deliberate: in a regulated migration the transformed value alone is not
sufficient evidence. What has to survive is the value, the rule that produced it,
and enough of the original to let an auditor re-derive or challenge the result.

Three principles hold throughout:

  Never silently alter.   A repair is recorded as a repair, with the original.
  Never silently drop.    A record that cannot be transformed is rejected to
                          quarantine with the rule it violated, never discarded.
  Never invent certainty. Where the source is genuinely ambiguous, the ambiguity
                          is carried forward as a flag rather than resolved by
                          picking a plausible answer and forgetting the question.
"""

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any, Optional
from zoneinfo import ZoneInfo

# Characters that show up when UTF-8 bytes are read as Latin-1. Their presence in
# text that is otherwise Spanish is the signal that double encoding happened.
MOJIBAKE_MARKERS = set("ÃÂÅÆÐÑ×ØÞßâãäåæçèéêë€‚ƒ„…†‡ˆ‰Š‹Œ")

NUMERIC_RE = re.compile(r"^\s*(?P<op>[<>]=?)?\s*(?P<num>-?\d+(?:[.,]\d+)?)\s*$")

STATUS_OK = "ok"
STATUS_REPAIRED = "repaired"
STATUS_FLAGGED = "flagged"
STATUS_REJECTED = "rejected"


@dataclass
class Decision:
    """The outcome of one transformation, with everything needed to audit it."""
    value: Any
    status: str
    rule: str                      # STTM field rule or control that applied
    requirement: str               # URS identifier this rule serves
    original: Any = None
    note: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status != STATUS_REJECTED


# ---------------------------------------------------------------------------
# Character encoding
# ---------------------------------------------------------------------------
def repair_double_encoding(text: Optional[str]) -> Decision:
    """
    Repair text whose UTF-8 bytes were stored as Latin-1 (DEF-04).

    The test is byte-level, not visual: if the string's Latin-1 bytes are
    themselves valid UTF-8 that decodes to something different, the string was
    double-encoded. 'Vilchez' with an accent stored correctly gives bytes that
    are NOT valid UTF-8; stored double-encoded it gives bytes that ARE.

    A repair is only applied when the original carries a mojibake marker. Text
    that round-trips but shows no marker is left alone and flagged instead:
    the repair would be a guess, and a guess applied silently to a donor name is
    exactly the kind of undocumented alteration this pack exists to prevent.
    """
    if text is None or text == "":
        return Decision(text, STATUS_OK, "decode(latin-1)", "URS-007")

    try:
        candidate = text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        # Bytes are not valid UTF-8 -- legitimate Latin-1 text.
        return Decision(text, STATUS_OK, "decode(latin-1)", "URS-007")

    if candidate == text:
        # Pure ASCII; nothing to repair.
        return Decision(text, STATUS_OK, "decode(latin-1)", "URS-007")

    if MOJIBAKE_MARKERS & set(text):
        return Decision(
            candidate, STATUS_REPAIRED, "repair_double_encoding", "URS-007",
            original=text,
            note="Latin-1 bytes decoded as valid UTF-8 and source carried a "
                 "mojibake marker; double encoding at rest (DEF-04)",
            extra={"source_bytes": text.encode("latin-1").hex()},
        )

    return Decision(
        text, STATUS_FLAGGED, "repair_double_encoding", "URS-007",
        original=text,
        note="Bytes round-trip as UTF-8 but no mojibake marker present; repair "
             "would be speculative. Original carried forward for review.",
    )


# ---------------------------------------------------------------------------
# Numeric results
# ---------------------------------------------------------------------------
def normalise_numeric(raw: Optional[str]) -> Decision:
    """
    Parse a free-text numeric result (DEF-03).

    Handles whitespace padding and comma decimal separators, and preserves
    censoring. A value recorded as '<0.10' is NOT parsed as 0.10: 'below the
    limit of detection' and 'measured 0.10' are different assertions, and
    collapsing them destroys information the laboratory recorded on purpose.
    The comparator is returned separately so both survive.
    """
    if raw is None or raw.strip() == "":
        return Decision(None, STATUS_OK, "normalise_numeric", "URS-006",
                        original=raw, extra={"operator": None})

    m = NUMERIC_RE.match(raw)
    if not m:
        return Decision(
            None, STATUS_REJECTED, "normalise_numeric", "URS-006",
            original=raw, note=f"unparseable numeric result {raw!r}",
            extra={"operator": None, "quarantine_reason": "QTN-04"},
        )

    operator = m.group("op")
    number = float(m.group("num").replace(",", "."))

    if operator:
        # Censored: the numeric column stays null, the comparator carries the
        # meaning, and the raw string is retained by the caller.
        return Decision(
            None, STATUS_FLAGGED, "normalise_numeric", "URS-006",
            original=raw,
            note=f"censored value; comparator {operator} preserved, numeric left null",
            extra={"operator": operator, "censored_bound": number},
        )

    status = STATUS_REPAIRED if raw != raw.strip() or "," in raw else STATUS_OK
    note = ""
    if "," in raw:
        note = "comma decimal separator normalised"
    elif raw != raw.strip():
        note = "whitespace padding stripped"

    return Decision(number, status, "normalise_numeric", "URS-006",
                    original=raw, note=note, extra={"operator": None})


def convert_unit(value: Optional[float], from_unit: Optional[str],
                 to_unit: Optional[str], conversion_map: dict) -> Decision:
    """
    Apply a unit conversion (DEF-02).

    Haemoglobin is g/dL in the legacy system and g/L in the target. Copying the
    number and relabelling the unit yields a value wrong by a factor of ten that
    is perfectly well-formed and passes every schema check. The factor is
    returned so it lands in its own column and the arithmetic stays auditable
    rather than buried in this function.
    """
    if not from_unit or not to_unit:
        return Decision(value, STATUS_OK, "convert_unit", "URS-006",
                        extra={"factor": None})

    if value is None and from_unit != to_unit:
        # No value to convert, but the unit mismatch is still a property of the
        # record and still has to be visible. Returning 'ok' here was a real
        # control gap: censored results carry a source unit that differs from
        # the target one, and reporting nothing left that difference invisible
        # on every row whose value was below the limit of detection.
        return Decision(
            None, STATUS_FLAGGED, "convert_unit", "URS-006",
            note=f"unit differs ({from_unit} -> {to_unit}) but the value is "
                 f"null, so no conversion was applied",
            extra={"factor": conversion_map.get(f"{from_unit}->{to_unit}"),
                   "value_absent": True},
        )

    if value is None:
        return Decision(value, STATUS_OK, "convert_unit", "URS-006",
                        extra={"factor": None})

    if from_unit == to_unit:
        return Decision(value, STATUS_OK, "convert_unit", "URS-006",
                        extra={"factor": 1.0})

    key = f"{from_unit}->{to_unit}"
    factor = conversion_map.get(key)
    if factor is None:
        return Decision(
            value, STATUS_REJECTED, "convert_unit", "URS-006",
            original=value,
            note=f"no conversion defined for {key}; value not migrated rather "
                 f"than migrated under the wrong unit",
            extra={"factor": None, "quarantine_reason": "QTN-04"},
        )

    return Decision(
        round(value * factor, 6), STATUS_REPAIRED, "convert_unit", "URS-006",
        original=value, note=f"converted {key} by factor {factor}",
        extra={"factor": factor},
    )


# ---------------------------------------------------------------------------
# Controlled vocabularies
# ---------------------------------------------------------------------------
def map_result_code(code: Optional[str], code_map: dict) -> Decision:
    """
    Map a legacy result code to the target vocabulary (DEF-08).

    The single highest-criticality transformation in this migration: this field
    carries the screening outcome that determines whether a unit of blood is
    released or destroyed.

    Fifteen years of accreted synonyms must collapse (NR, N and NEG all mean
    non-reactive) WITHOUT collapsing distinct clinical states. INITIAL_REACTIVE
    and REPEAT_REACTIVE are different determinations and must not merge. An
    unrecognised code is rejected, never defaulted -- guessing here is the one
    failure mode with a direct patient-safety consequence.
    """
    if code is None or code.strip() == "":
        return Decision(None, STATUS_REJECTED, "map_result_code", "URS-003",
                        original=code, note="mandatory result code is empty",
                        extra={"quarantine_reason": "QTN-04"})

    key = code.strip().upper()
    if key not in code_map:
        return Decision(
            None, STATUS_REJECTED, "map_result_code", "URS-005",
            original=code,
            note=f"result code {code!r} absent from the controlled vocabulary; "
                 f"rejected rather than defaulted",
            extra={"quarantine_reason": "QTN-03"},
        )

    mapped = code_map[key]
    if key == "9999":
        return Decision(
            mapped, STATUS_FLAGGED, "map_result_code", "URS-005",
            original=code,
            note="undocumented legacy sentinel (DEF-08); mapped to NOT_PERFORMED, "
                 "which is the absence of a result rather than a result",
        )

    status = STATUS_REPAIRED if mapped != key else STATUS_OK
    return Decision(mapped, status, "map_result_code", "URS-005", original=code)


def map_assay(code: Optional[str], catalogue: dict) -> Decision:
    """
    Resolve a legacy assay code to the target vocabulary (DEF-01).

    Two legacy assays have no agreed target code. The row is NOT rejected: the
    result itself is valid laboratory data and discarding it would lose history.
    It is loaded with the legacy code intact and the gap reported for a
    documented disposition by the laboratory subject matter expert.

    A reported gap and a silent null look identical in the target table. The
    difference is entirely in whether someone was told.
    """
    if code is None or code.strip() == "":
        return Decision(None, STATUS_REJECTED, "map_assay", "URS-005",
                        original=code, note="assay code is empty",
                        extra={"quarantine_reason": "QTN-04"})

    key = code.strip().upper()
    entry = catalogue.get(key)
    if entry is None:
        return Decision(
            None, STATUS_REJECTED, "map_assay", "URS-005", original=code,
            note=f"assay code {code!r} not present in the legacy catalogue",
            extra={"quarantine_reason": "QTN-05"},
        )

    if entry.get("loinc") is None:
        return Decision(
            None, STATUS_FLAGGED, "map_assay", "URS-005", original=code,
            note=f"assay {key} ({entry.get('name')}) has no target vocabulary "
                 f"code; MAPPING GAP requiring disposition, loaded with the "
                 f"legacy code retained",
            extra={"gap": True, "legacy_name": entry.get("name")},
        )

    return Decision(entry["loinc"], STATUS_OK, "map_assay", "URS-005",
                    original=code, extra={"loinc_name": entry.get("loinc_name")})


# ---------------------------------------------------------------------------
# Temporal
# ---------------------------------------------------------------------------
def to_utc(local_str: Optional[str], tz_name: str,
           fmt: str = "%d/%m/%Y %H:%M:%S") -> Decision:
    """
    Convert a legacy local wall-clock timestamp to UTC (DEF-05).

    The legacy schema has no offset column, so the offset must be inferred from
    the collecting site. Sites span two of them: peninsular Spain and the Canary
    Islands differ by an hour.

    Instants inside the October DST fallback hour occur twice and are genuinely
    ambiguous -- the source cannot say which. They are resolved to the first
    occurrence for determinism, and FLAGGED. The alternative, picking one
    silently, would produce a timestamp indistinguishable from a certain one,
    which is worse than being an hour out: it is being an hour out invisibly.
    """
    if local_str is None or local_str.strip() == "":
        return Decision(None, STATUS_REJECTED, "to_utc", "URS-004",
                        original=local_str, note="mandatory timestamp is empty",
                        extra={"quarantine_reason": "QTN-04"})

    try:
        naive = dt.datetime.strptime(local_str.strip(), fmt)
    except ValueError:
        return Decision(None, STATUS_REJECTED, "to_utc", "URS-004",
                        original=local_str,
                        note=f"timestamp {local_str!r} does not match {fmt}",
                        extra={"quarantine_reason": "QTN-04"})

    tz = ZoneInfo(tz_name)
    first = naive.replace(tzinfo=tz, fold=0)
    second = naive.replace(tzinfo=tz, fold=1)
    ambiguous = first.utcoffset() != second.utcoffset()

    chosen = first
    utc = chosen.astimezone(dt.timezone.utc)
    offset = chosen.utcoffset()

    if ambiguous:
        return Decision(
            utc, STATUS_FLAGGED, "to_utc", "URS-004", original=local_str,
            note=f"local time occurs twice at the {tz_name} DST transition; "
                 f"resolved to the first occurrence (UTC{_fmt_offset(offset)}), "
                 f"true value may be one hour later",
            extra={"source_timezone": tz_name,
                   "utc_offset": _fmt_offset(offset),
                   "ambiguous": True,
                   "alternative_utc": second.astimezone(dt.timezone.utc).isoformat()},
        )

    return Decision(utc, STATUS_OK, "to_utc", "URS-004", original=local_str,
                    extra={"source_timezone": tz_name,
                           "utc_offset": _fmt_offset(offset),
                           "ambiguous": False})


def _fmt_offset(offset: Optional[dt.timedelta]) -> str:
    if offset is None:
        return "?"
    total = int(offset.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    return f"{sign}{total // 3600:02d}:{(total % 3600) // 60:02d}"
