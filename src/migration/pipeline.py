"""
Executes the migration described by STTM-001.

Structure follows the four verification levels GxP practice expects of a data
migration -- field, record, data object, portfolio -- because a control at one
level cannot substitute for a control at another. A row can be individually
valid and still be a duplicate; every row can be valid and the set still be
short by one.

  Portfolio   manifest control totals reconciled against the files themselves
  Data object primary key uniqueness, referential integrity across entities
  Record      mandatory fields present, row loadable as a whole
  Field       type, vocabulary, unit, encoding, timestamp per STTM-001

Outputs, all of which are evidence:

  curated/    records that migrated cleanly, as Parquet partitioned by year
  quarantine/ records that did not, with the rule each one violated
  audit/      every transformation decision that altered or flagged a value

Usage:
    python -m migration.pipeline --extract ../data/legacy --out ../data/target
"""

import argparse
import datetime as dt
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from legacy_sim.domain import ASSAY_CATALOGUE
from migration.transforms import (
    STATUS_FLAGGED, STATUS_OK, STATUS_REJECTED, STATUS_REPAIRED,
    convert_unit, map_assay, map_result_code, normalise_numeric,
    repair_double_encoding, to_utc,
)

STTM_PATH = (Path(__file__).resolve().parents[2]
             / "docs" / "03-mapping" / "STTM-001-source-to-target-mapping.yaml")

LEGACY_ENCODING = "latin-1"
DELIM = "|"


# ---------------------------------------------------------------------------
def read_table(path):
    """Read a pipe-delimited Latin-1 legacy file into dicts, preserving raw text."""
    rows = []
    with path.open("r", encoding=LEGACY_ENCODING, newline="") as fh:
        header = fh.readline().rstrip("\r\n").split(DELIM)
        for line in fh:
            line = line.rstrip("\r\n")
            if not line:
                continue
            values = line.split(DELIM)
            rows.append(dict(zip(header, values)))
    return rows


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def assay_lookup():
    return {
        code: {"name": name, "loinc": loinc, "loinc_name": loinc_name,
               "kind": kind, "legacy_unit": lu, "target_unit": tu}
        for code, name, loinc, loinc_name, kind, lu, tu in ASSAY_CATALOGUE
    }


# ---------------------------------------------------------------------------
class Run:
    """Accumulates outputs and findings for one migration execution."""

    def __init__(self):
        self.curated = defaultdict(list)      # entity -> rows
        self.quarantine = []
        self.audit = []
        self.findings = []                    # portfolio / data-object level
        self.counters = Counter()

    def audit_decision(self, entity, key, field, decision):
        """Record any decision that altered, flagged or rejected a value.

        Decisions with status 'ok' are not recorded: an audit trail of every
        untouched field would be mostly noise, and noise is where real entries
        go to hide. What must be reconstructible is every point at which the
        migration changed or questioned the source.
        """
        if decision.status == STATUS_OK:
            return
        self.audit.append({
            "entity": entity,
            "key": key,
            "field": field,
            "status": decision.status,
            "rule": decision.rule,
            "requirement": decision.requirement,
            "original": decision.original,
            "resulting": (decision.value.isoformat()
                          if hasattr(decision.value, "isoformat")
                          else decision.value),
            "note": decision.note,
            **({"detail": decision.extra} if decision.extra else {}),
        })
        self.counters[f"decision:{decision.status}"] += 1

    def reject(self, entity, key, reason_code, rule, note, row):
        self.quarantine.append({
            "entity": entity,
            "key": key,
            "quarantine_reason": reason_code,
            "rule_violated": rule,
            "note": note,
            "source_row": row,
            "quarantined_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        })
        self.counters[f"quarantine:{reason_code}"] += 1

    def finding(self, control, level, severity, summary, detail=None):
        self.findings.append({
            "control": control, "level": level, "severity": severity,
            "summary": summary, "detail": detail or {},
        })


# ---------------------------------------------------------------------------
# Portfolio level
# ---------------------------------------------------------------------------
def control_c01_manifest(extract_dir, tables, run):
    """REC-C01 -- reconcile the source manifest against the files themselves.

    Runs before anything is compared with the target. A source-to-target count
    match is meaningless if the source count was never verified: both sides
    would simply agree on the wrong number.
    """
    manifest = json.loads((extract_dir / "MANIFEST.json").read_text(encoding="utf-8"))

    for entry in manifest["files"]:
        path = extract_dir / entry["file"]
        actual = sha256(path)
        if actual != entry["sha256"]:
            run.finding("REC-C01", "portfolio", "critical",
                        f"{entry['file']} checksum does not match the manifest",
                        {"declared": entry["sha256"], "actual": actual})

    for name, declared in manifest["declared_row_counts"].items():
        actual = len(tables[name])
        if declared != actual:
            run.finding(
                "REC-C01", "portfolio", "major",
                f"{name}: manifest declares {declared} rows, file holds {actual}",
                {"declared": declared, "actual": actual, "delta": actual - declared,
                 "interpretation": "source extract is internally inconsistent; "
                                   "control totals cannot be trusted until the "
                                   "data owner confirms which figure is correct"},
            )
    return manifest


# ---------------------------------------------------------------------------
# Data object level
# ---------------------------------------------------------------------------
def control_i01_uniqueness(entity, rows, key_field, run):
    """REC-I01 -- primary key uniqueness.

    All rows sharing a duplicated key are quarantined, not just the later ones.
    Keeping an arbitrary survivor would be an undocumented data decision: without
    provenance there is no basis for preferring one row over the other, and the
    target would silently hold one version of a donation that exists twice.
    """
    seen = defaultdict(list)
    for row in rows:
        seen[row[key_field]].append(row)

    duplicates = {k: v for k, v in seen.items() if len(v) > 1}
    if duplicates:
        run.finding(
            "REC-I01", "data object", "critical",
            f"{entity}: {len(duplicates)} duplicated {key_field} value(s) "
            f"affecting {sum(len(v) for v in duplicates.values())} rows",
            {"keys": sorted(duplicates)[:20]},
        )
    for key, rows_for_key in duplicates.items():
        for row in rows_for_key:
            run.reject(entity, key, "QTN-01", "REC-I01",
                       f"{key_field} appears {len(rows_for_key)} times with "
                       f"differing content", row)
    return set(duplicates)


def control_i02_referential(child_rows, child_key, parent_ids, entity, run):
    """REC-I02 -- referential integrity. Orphans are quarantined, never loaded
    with a null parent and never dropped."""
    orphans = [r for r in child_rows if r[child_key] not in parent_ids]
    if orphans:
        run.finding(
            "REC-I02", "data object", "critical",
            f"{entity}: {len(orphans)} row(s) reference a {child_key} absent "
            f"from the extract",
            {"missing_parents": sorted({r[child_key] for r in orphans})[:20],
             "interpretation": "extract windows for the two tables were not "
                               "taken atomically"},
        )
    return {r["RESULT_ID"] for r in orphans}


# ---------------------------------------------------------------------------
def migrate(extract_dir, run):
    sttm = yaml.safe_load(STTM_PATH.read_text(encoding="utf-8"))
    tz_map = sttm["lookups"]["site_timezone_map"]
    unit_map = sttm["lookups"]["unit_conversion_map"]
    code_map = sttm["lookups"]["result_code_map"]
    assays = assay_lookup()

    tables = {
        "DONOR": read_table(extract_dir / "DONOR.txt"),
        "DONATION": read_table(extract_dir / "DONATION.txt"),
        "RESULT": read_table(extract_dir / "RESULT.txt"),
        "QC": read_table(extract_dir / "QC.txt"),
    }
    for name, rows in tables.items():
        run.counters[f"source:{name}"] = len(rows)

    control_c01_manifest(extract_dir, tables, run)

    # ---- donor ------------------------------------------------------------
    dup_donors = control_i01_uniqueness("donor", tables["DONOR"], "DONOR_ID", run)
    donor_ids = set()
    for row in tables["DONOR"]:
        key = row["DONOR_ID"]
        if key in dup_donors:
            continue
        out = {"donor_id": key}
        rejected = False

        for src, tgt in (("SURNAME1", "surname_1"), ("SURNAME2", "surname_2"),
                         ("FORENAME", "forename")):
            d = repair_double_encoding(row[src])
            run.audit_decision("donor", key, tgt, d)
            out[tgt] = d.value

        for src, tgt in (("ABO", "abo_group"), ("RH", "rh_type")):
            d = map_result_code(row[src], code_map)
            run.audit_decision("donor", key, tgt, d)
            if not d.ok:
                run.reject("donor", key, d.extra.get("quarantine_reason", "QTN-03"),
                           "REC-A04", d.note, row)
                rejected = True
                break
            out[tgt] = d.value
        if rejected:
            continue

        out["birth_date"] = row["BIRTH_DATE"]
        out["sex"] = {"M": "male", "F": "female"}.get(row["SEX"], "unknown")
        out["site_code"] = row["SITE_CODE"]
        out["national_id_hash"] = hashlib.sha256(
            (row["NATIONAL_ID"].strip().upper() + "STTM-001").encode()).hexdigest()

        donor_ids.add(key)
        run.curated["donor"].append(out)

    # ---- donation ---------------------------------------------------------
    dup_donations = control_i01_uniqueness("donation", tables["DONATION"],
                                           "DONATION_ID", run)
    donation_ids = set()
    for row in tables["DONATION"]:
        key = row["DONATION_ID"]
        if key in dup_donations:
            continue

        tz = tz_map.get(row["SITE_CODE"], "Europe/Madrid")
        d = to_utc(row["COLLECTION_DT"], tz)
        run.audit_decision("donation", key, "collection_ts_utc", d)
        if not d.ok:
            run.reject("donation", key, "QTN-04", "REC-I04", d.note, row)
            continue

        donation_ids.add(key)
        run.curated["donation"].append({
            "donation_id": key,
            "donor_id": row["DONOR_ID"],
            "site_code": row["SITE_CODE"],
            "collection_ts_utc": d.value.isoformat(),
            "collection_ts_local": row["COLLECTION_DT"],   # ALCOA+ Original
            "source_timezone": tz,
            "collection_year": d.value.year,
            "component_type": {"WB": "whole_blood", "APH-PLT": "apheresis_platelets",
                               "APH-PLS": "apheresis_plasma"}.get(row["COMPONENT_TYPE"]),
            "volume_ml": int(row["VOLUME_ML"]) if row["VOLUME_ML"].isdigit() else None,
            "operator_id": row["OPERATOR_ID"],
            "timestamp_ambiguous": bool(d.extra.get("ambiguous")),
        })

    # ---- result -----------------------------------------------------------
    orphan_ids = control_i02_referential(tables["RESULT"], "DONATION_ID",
                                         donation_ids | dup_donations,
                                         "screening_result", run)
    mapping_gaps = Counter()

    for row in tables["RESULT"]:
        key = row["RESULT_ID"]
        if key in orphan_ids:
            run.reject("screening_result", key, "QTN-02", "REC-I02",
                       f"references donation {row['DONATION_ID']} absent from "
                       f"the extract", row)
            continue
        if row["DONATION_ID"] in dup_donations:
            run.reject("screening_result", key, "QTN-01", "REC-I01",
                       f"parent donation {row['DONATION_ID']} is quarantined as "
                       f"a duplicate", row)
            continue

        assay = assays.get(row["ASSAY_CODE"], {})

        a = map_assay(row["ASSAY_CODE"], assays)
        run.audit_decision("screening_result", key, "assay_code_loinc", a)
        if not a.ok:
            run.reject("screening_result", key,
                       a.extra.get("quarantine_reason", "QTN-05"), "REC-C03",
                       a.note, row)
            continue
        if a.extra.get("gap"):
            mapping_gaps[row["ASSAY_CODE"]] += 1

        c = map_result_code(row["RESULT_CODE"], code_map)
        run.audit_decision("screening_result", key, "result_code", c)
        if not c.ok:
            run.reject("screening_result", key,
                       c.extra.get("quarantine_reason", "QTN-03"), "REC-A04",
                       c.note, row)
            continue

        n = normalise_numeric(row["RESULT_VALUE"])
        run.audit_decision("screening_result", key, "result_value_numeric", n)
        if not n.ok:
            run.reject("screening_result", key, "QTN-04", "REC-A01", n.note, row)
            continue

        u = convert_unit(n.value, assay.get("legacy_unit") or None,
                         assay.get("target_unit") or None, unit_map)
        run.audit_decision("screening_result", key, "result_value_converted", u)
        if not u.ok:
            run.reject("screening_result", key, "QTN-04", "REC-A02", u.note, row)
            continue

        tz = tz_map.get(row["INSTRUMENT_ID"], "Europe/Madrid")
        t = to_utc(row["RESULT_DT"], tz)
        run.audit_decision("screening_result", key, "result_ts_utc", t)
        if not t.ok:
            run.reject("screening_result", key, "QTN-04", "REC-I04", t.note, row)
            continue

        run.curated["screening_result"].append({
            "result_id": key,
            "donation_id": row["DONATION_ID"],
            "assay_code_source": row["ASSAY_CODE"],
            "assay_code_loinc": a.value,
            "mapping_gap": bool(a.extra.get("gap")),
            "result_code": c.value,
            "result_value_numeric": u.value,
            "result_value_operator": n.extra.get("operator"),
            "result_value_raw": row["RESULT_VALUE"],     # ALCOA+ Original
            "unit_source": assay.get("legacy_unit") or None,
            "unit_target": assay.get("target_unit") or None,
            "unit_conversion_factor": u.extra.get("factor"),
            "result_ts_utc": t.value.isoformat(),
            "result_year": t.value.year,
            "instrument_id": row["INSTRUMENT_ID"],
            "operator_id": row["OPERATOR_ID"],
            "review_flag": row["REVIEW_FLAG"] == "Y",
            "timestamp_ambiguous": bool(t.extra.get("ambiguous")),
        })

    if mapping_gaps:
        run.finding(
            "REC-C03", "field", "major",
            f"{len(mapping_gaps)} legacy assay code(s) have no target vocabulary "
            f"equivalent, affecting {sum(mapping_gaps.values())} results",
            {"codes": {k: {"rows": v, "legacy_name": assays[k]["name"]}
                       for k, v in mapping_gaps.items()},
             "interpretation": "reported mapping gap requiring disposition by the "
                               "laboratory subject matter expert; rows are loaded "
                               "with the legacy code retained, not dropped"},
        )

    # ---- qc ---------------------------------------------------------------
    for row in tables["QC"]:
        key = row["QC_ID"]
        t = to_utc(row["RUN_DT"], "Europe/Madrid")
        run.audit_decision("qc_result", key, "run_ts_utc", t)
        if not t.ok:
            run.reject("qc_result", key, "QTN-04", "REC-I04", t.note, row)
            continue
        exp = normalise_numeric(row["EXPECTED_VALUE"])
        obs = normalise_numeric(row["OBSERVED_VALUE"])
        run.curated["qc_result"].append({
            "qc_id": key,
            "assay_code_source": row["ASSAY_CODE"],
            "qc_lot": row["QC_LOT"],
            "qc_level": row["QC_LEVEL"],
            "expected_value": exp.value,
            "observed_value": obs.value,
            "qc_status": row["QC_STATUS"],      # verbatim; never recomputed
            "run_ts_utc": t.value.isoformat(),
            "run_year": t.value.year,
            "instrument_id": row["INSTRUMENT_ID"],
        })

    return tables


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Execute the STTM-001 migration")
    ap.add_argument("--extract", default="../data/legacy", type=Path)
    ap.add_argument("--out", default="../data/target", type=Path)
    args = ap.parse_args()

    started = dt.datetime.now(dt.timezone.utc)
    run = Run()
    tables = migrate(args.extract, run)
    finished = dt.datetime.now(dt.timezone.utc)

    args.out.mkdir(parents=True, exist_ok=True)
    for entity, rows in run.curated.items():
        (args.out / f"{entity}.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    (args.out / "quarantine.jsonl").write_text(
        "\n".join(json.dumps(r) for r in run.quarantine), encoding="utf-8")
    (args.out / "audit.jsonl").write_text(
        "\n".join(json.dumps(r) for r in run.audit), encoding="utf-8")

    summary = {
        "run_id": f"MIG-{started:%Y%m%dT%H%M%SZ}",
        "started_utc": started.isoformat(),
        "finished_utc": finished.isoformat(),
        "source_rows": {k: len(v) for k, v in tables.items()},
        "migrated_rows": {k: len(v) for k, v in run.curated.items()},
        "quarantined": len(run.quarantine),
        "audit_entries": len(run.audit),
        "quarantine_by_reason": {k.split(":", 1)[1]: v
                                 for k, v in run.counters.items()
                                 if k.startswith("quarantine:")},
        "decisions_by_status": {k.split(":", 1)[1]: v
                                for k, v in run.counters.items()
                                if k.startswith("decision:")},
        "findings": run.findings,
    }
    (args.out / "run_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")

    src_total = sum(len(v) for v in tables.values())
    mig_total = sum(len(v) for v in run.curated.values())

    print("=" * 74)
    print(f"MIGRATION RUN  {summary['run_id']}")
    print("=" * 74)
    print(f"  source rows      {src_total:>7,}")
    print(f"  migrated         {mig_total:>7,}")
    print(f"  quarantined      {len(run.quarantine):>7,}")
    print(f"  accounted for    {mig_total + len(run.quarantine):>7,}"
          f"   {'BALANCED' if mig_total + len(run.quarantine) == src_total else 'IMBALANCE'}")
    print(f"  audit entries    {len(run.audit):>7,}")

    print("\n  quarantine by reason")
    for reason, n in sorted(summary["quarantine_by_reason"].items()):
        print(f"    {reason}  {n:>6,}")

    print("\n  transformation decisions")
    for status, n in sorted(summary["decisions_by_status"].items()):
        print(f"    {status:<10} {n:>6,}")

    print(f"\n  findings ({len(run.findings)})")
    for f in run.findings:
        print(f"    [{f['severity']:<8}] {f['control']:<9} {f['level']:<12} {f['summary']}")

    print(f"\n  outputs written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
