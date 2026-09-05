"""
Generates a simulated extract from the legacy blood-screening LIS.

The output imitates what a 15-year-old laboratory system actually hands over at
decommissioning time: pipe-delimited flat files, Latin-1 encoded, local wall-clock
timestamps with no offset, free-text numeric fields, and a control-total manifest.

Determinism is a validation property, not a convenience: the extract is fully
reproducible from the seed, so any reviewer can regenerate the exact dataset the
evidence was produced from.

Usage:
    python -m legacy_sim.generate_extract --out data/legacy --seed 20260905
"""

import argparse
import datetime as dt
import hashlib
import json
import random
from pathlib import Path

from legacy_sim.domain import (
    ABO_GROUPS, ASSAY_CATALOGUE, FORENAMES, RH_TYPES, SITES, SURNAMES,
)

LEGACY_ENCODING = "latin-1"
DELIM = "|"

EXTRACT_WINDOW_START = dt.date(2020, 1, 1)
EXTRACT_WINDOW_END = dt.date(2024, 12, 31)

# Local wall-clock instants inside the October DST fallback hour in Europe/Madrid.
# 02:30 occurs twice on these dates; the legacy schema cannot express which one.
DST_AMBIGUOUS_LOCAL = [
    dt.datetime(2020, 10, 25, 2, 30),
    dt.datetime(2021, 10, 31, 2, 30),
    dt.datetime(2022, 10, 30, 2, 30),
    dt.datetime(2023, 10, 29, 2, 30),
    dt.datetime(2024, 10, 27, 2, 30),
]

CHECK_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"


def _mojibake(text):
    """Reproduce UTF-8 bytes that were stored as Latin-1 -- corruption already at rest."""
    return text.encode("utf-8").decode("latin-1")


def _sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _write(path, header, rows):
    with path.open("w", encoding=LEGACY_ENCODING, newline="\r\n") as fh:
        fh.write(DELIM.join(header) + "\n")
        for row in rows:
            fh.write(DELIM.join("" if v is None else str(v) for v in row) + "\n")


def build(seed, n_donors, n_donations):
    rng = random.Random(seed)
    injected = []

    def log(defect_id, table, key, note):
        injected.append({"defect_id": defect_id, "table": table, "key": key, "note": note})

    # ---------------------------------------------------------------- donors
    donors = []
    for i in range(1, n_donors + 1):
        donor_id = "D%06d" % i
        forename = rng.choice(FORENAMES)
        surname1 = rng.choice(SURNAMES)
        surname2 = rng.choice(SURNAMES)
        site = rng.choice(SITES)
        birth = dt.date(rng.randint(1955, 2004), rng.randint(1, 12), rng.randint(1, 28))

        # DEF-04: ~2% of donor names were double-encoded at rest years ago.
        if rng.random() < 0.02:
            forename = _mojibake(forename)
            surname1 = _mojibake(surname1)
            log("DEF-04", "DONOR", donor_id,
                "forename and first surname double-encoded at rest")

        national_id = "%08d%s" % (rng.randint(10000000, 99999999), rng.choice(CHECK_LETTERS))
        donors.append([
            donor_id, national_id, surname1, surname2, forename,
            birth.isoformat(),
            rng.choice(["M", "F"]),
            rng.choice(ABO_GROUPS), rng.choice(RH_TYPES),
            site[0], "", "A",
        ])

    donor_ids = [d[0] for d in donors]

    # ------------------------------------------------------------- donations
    donations = []
    span_days = (EXTRACT_WINDOW_END - EXTRACT_WINDOW_START).days
    for i in range(1, n_donations + 1):
        donation_id = "%s-%d-%06d" % (rng.choice(SITES)[0], 2020 + (i % 5), i)
        donor_id = rng.choice(donor_ids)
        site = rng.choice(SITES)

        # DEF-05: a handful of collections land in the DST fallback hour.
        if rng.random() < 0.004:
            collected = rng.choice(DST_AMBIGUOUS_LOCAL)
            log("DEF-05", "DONATION", donation_id,
                "local time %s occurs twice; no offset recorded"
                % collected.strftime("%Y-%m-%d %H:%M"))
        else:
            collected = dt.datetime.combine(
                EXTRACT_WINDOW_START + dt.timedelta(days=rng.randint(0, span_days)),
                dt.time(rng.randint(8, 19), rng.choice([0, 15, 30, 45])),
            )

        donations.append([
            donation_id, donor_id, site[0],
            collected.strftime("%d/%m/%Y %H:%M:%S"),   # legacy format, no offset
            rng.choice(["WB", "APH-PLT", "APH-PLS"]),
            rng.choice([450, 450, 450, 500]),
            "OP%03d" % rng.randint(1, 40),
            "A",
        ])

    # DEF-06: duplicate donation identifiers from a historical site-database merge.
    for _ in range(4):
        original = rng.choice(donations)
        clone = list(original)
        clone[5] = 500 if original[5] != 500 else 450   # differing content
        clone[6] = "OP%03d" % rng.randint(1, 40)
        donations.append(clone)
        log("DEF-06", "DONATION", original[0],
            "duplicate donation id with differing volume/operator")

    # ---------------------------------------------------------------- results
    results = []
    rid = 0
    for row in donations:
        donation_id = row[0]
        collected = dt.datetime.strptime(row[3], "%d/%m/%Y %H:%M:%S")
        for assay in ASSAY_CATALOGUE:
            legacy_code, _name, loinc, _ln, kind, legacy_unit, _tu = assay

            # Retired assay only appears in the earlier part of the window.
            if legacy_code == "LOC901" and collected.year > 2021:
                continue

            rid += 1
            result_id = "R%08d" % rid
            result_dt = collected + dt.timedelta(hours=rng.randint(2, 30))

            if kind == "QUAL":
                if legacy_code == "IMM020":
                    code, value = rng.choice(ABO_GROUPS), ""
                elif legacy_code == "IMM021":
                    code, value = rng.choice(RH_TYPES), ""
                else:
                    code = rng.choices(
                        ["NR", "N", "NEG", "IR", "RR", "INV"],
                        weights=[70, 12, 10, 4, 2, 2],
                    )[0]
                    value = ""
            else:
                # DEF-03: signal-to-cutoff ratios held as free text.
                roll = rng.random()
                if roll < 0.06:
                    value = "<0.10"
                    log("DEF-03", "RESULT", result_id,
                        "censored value below limit of detection")
                elif roll < 0.12:
                    value = "  %.2f  " % rng.uniform(0.02, 0.9)
                    log("DEF-03", "RESULT", result_id, "whitespace-padded numeric")
                elif roll < 0.17:
                    value = ("%.2f" % rng.uniform(0.02, 0.9)).replace(".", ",")
                    log("DEF-03", "RESULT", result_id, "comma decimal separator")
                elif legacy_code == "LOC900":
                    value = "%.1f" % rng.uniform(12.5, 17.5)   # g/dL, target wants g/L
                else:
                    value = "%.2f" % rng.uniform(0.02, 0.9)

                try:
                    sco = float(value.strip().replace(",", "."))
                except ValueError:
                    sco = 0.05
                code = "R" if sco >= 1.0 else "NR"

            # DEF-08: undocumented sentinel meaning 'test not performed'.
            if rng.random() < 0.008:
                code, value = "9999", ""
                log("DEF-08", "RESULT", result_id, "sentinel 9999 = test not performed")

            if legacy_code == "LOC900":
                log("DEF-02", "RESULT", result_id,
                    "haemoglobin in g/dL; target expects g/L")
            if loinc is None:
                log("DEF-01", "RESULT", result_id,
                    "assay %s has no target vocabulary code" % legacy_code)

            results.append([
                result_id, donation_id, legacy_code, code, value, legacy_unit,
                result_dt.strftime("%d/%m/%Y %H:%M:%S"),
                "INS%02d" % rng.randint(1, 6),
                "OP%03d" % rng.randint(1, 40),
                rng.choice(["", "", "", "Y"]),
            ])

    # DEF-07: orphan results -- extract windows for the two tables were not atomic.
    for k in range(6):
        rid += 1
        result_id = "R%08d" % rid
        ghost = "BCN01-2019-%06d" % (900000 + k)
        results.append([
            result_id, ghost, "SER010", "NR", "0.12", "S/CO",
            "14/03/2019 09:15:00", "INS01", "OP007", "",
        ])
        log("DEF-07", "RESULT", result_id,
            "references donation %s absent from extract" % ghost)

    # -------------------------------------------------------------------- QC
    qc = []
    sco_assays = [a for a in ASSAY_CATALOGUE if a[4] == "SCO"]
    for i in range(1, 401):
        assay = rng.choice(sco_assays)
        level = rng.choice(["L1", "L2"])
        expected = 0.25 if level == "L1" else 3.50
        observed = expected * rng.uniform(0.88, 1.12)
        run_day = EXTRACT_WINDOW_START + dt.timedelta(days=rng.randint(0, span_days))
        qc.append([
            "QC%06d" % i, assay[0], "LOT%d" % rng.randint(100, 140), level,
            "%.2f" % expected, "%.2f" % observed,
            run_day.strftime("%d/%m/%Y") + " %02d:00:00" % rng.randint(6, 9),
            "INS%02d" % rng.randint(1, 6),
            "PASS" if 0.8 <= observed / expected <= 1.2 else "FAIL",
        ])

    return donors, donations, results, qc, injected


def main():
    ap = argparse.ArgumentParser(description="Generate simulated legacy LIS extract")
    ap.add_argument("--out", default="data/legacy", type=Path)
    ap.add_argument("--oracle", default="data/_oracle", type=Path)
    ap.add_argument("--seed", default=20260905, type=int)
    ap.add_argument("--donors", default=500, type=int)
    ap.add_argument("--donations", default=1200, type=int)
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    args.oracle.mkdir(parents=True, exist_ok=True)

    donors, donations, results, qc, injected = build(
        args.seed, args.donors, args.donations)

    _write(args.out / "DONOR.txt",
           ["DONOR_ID", "NATIONAL_ID", "SURNAME1", "SURNAME2", "FORENAME",
            "BIRTH_DATE", "SEX", "ABO", "RH", "SITE_CODE",
            "FIRST_DONATION_DT", "REC_STATUS"], donors)
    _write(args.out / "DONATION.txt",
           ["DONATION_ID", "DONOR_ID", "SITE_CODE", "COLLECTION_DT",
            "COMPONENT_TYPE", "VOLUME_ML", "OPERATOR_ID", "REC_STATUS"], donations)
    _write(args.out / "RESULT.txt",
           ["RESULT_ID", "DONATION_ID", "ASSAY_CODE", "RESULT_CODE", "RESULT_VALUE",
            "UNIT", "RESULT_DT", "INSTRUMENT_ID", "OPERATOR_ID", "REVIEW_FLAG"], results)
    _write(args.out / "QC.txt",
           ["QC_ID", "ASSAY_CODE", "QC_LOT", "QC_LEVEL", "EXPECTED_VALUE",
            "OBSERVED_VALUE", "RUN_DT", "INSTRUMENT_ID", "QC_STATUS"], qc)

    # DEF-09: manifest counted RESULT before the last row was flushed.
    declared_results = len(results) - 1
    injected.append({
        "defect_id": "DEF-09", "table": "RESULT", "key": "MANIFEST",
        "note": "manifest declares %d, file holds %d" % (declared_results, len(results)),
    })

    manifest = {
        "extract_id": "EXT-%d" % args.seed,
        "source_system": "LEGACY-LIS v4.2 (decommissioning)",
        "extract_timestamp_local": "05/09/2026 23:10:00",
        "extract_window": {
            "from": EXTRACT_WINDOW_START.isoformat(),
            "to": EXTRACT_WINDOW_END.isoformat(),
        },
        "character_set": "ISO-8859-1",
        "delimiter": "|",
        "files": [],
        "declared_row_counts": {
            "DONOR": len(donors),
            "DONATION": len(donations),
            "RESULT": declared_results,
            "QC": len(qc),
        },
    }
    for name in ("DONOR", "DONATION", "RESULT", "QC"):
        p = args.out / (name + ".txt")
        manifest["files"].append({
            "file": p.name, "bytes": p.stat().st_size, "sha256": _sha256(p),
        })
    (args.out / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")

    (args.oracle / "injected_defects.json").write_text(
        json.dumps({"seed": args.seed, "injected": injected}, indent=2), encoding="utf-8")

    by_defect = {}
    for entry in injected:
        by_defect[entry["defect_id"]] = by_defect.get(entry["defect_id"], 0) + 1

    print("Legacy extract written to %s" % args.out)
    print("  DONOR    %6d" % len(donors))
    print("  DONATION %6d" % len(donations))
    print("  RESULT   %6d  (manifest declares %d)" % (len(results), declared_results))
    print("  QC       %6d" % len(qc))
    print("\nInjected defects (oracle -> %s):" % args.oracle)
    for defect_id in sorted(by_defect):
        print("  %s  %5d occurrence(s)" % (defect_id, by_defect[defect_id]))


if __name__ == "__main__":
    main()
