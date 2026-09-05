"""
Known-answer verification: did the migration detect what was actually injected?

The oracle written by the extract generator lists every injected defect and the
exact row it landed on. The migration code has never read that file. Comparing
the two afterwards gives recall per defect -- the share of injected occurrences
the controls actually caught -- rather than the much weaker claim that some
findings were produced.

Recall is the number that matters here. A migration control that catches 80% of
duplicate keys is not 80% of a control; it means one in five duplicated donation
records reaches the target unnoticed.

Usage:
    python -m reconciliation.known_answer --oracle ../data/_oracle --target ../data/target
"""

import argparse
import datetime as dt
import json
import sys
from collections import defaultdict
from pathlib import Path

from legacy_sim.defects import BY_ID, DEFECT_CATALOGUE


def load_jsonl(path):
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def detected_keys(audit, quarantine, findings):
    """Map each defect id to the set of source keys the migration flagged for it.

    The mapping is by the control that fired, not by defect id: the migration
    has no notion of DEF-04, it only knows that a repair rule matched. Naming the
    defect is done here, on the scoring side, which is what keeps the test honest.
    """
    found = defaultdict(set)

    for e in audit:
        rule, status = e["rule"], e["status"]
        detail = e.get("detail") or {}

        if rule == "repair_double_encoding" and status == "repaired":
            found["DEF-04"].add(e["key"])
        elif rule == "normalise_numeric" and status in ("repaired", "flagged"):
            found["DEF-03"].add(e["key"])
        elif (rule == "convert_unit" and status in ("repaired", "flagged")
                and detail.get("factor") not in (None, 1.0)):
            # 'flagged' matters as much as 'repaired': a censored result has no
            # value to convert, but its source and target units still disagree.
            found["DEF-02"].add(e["key"])
        elif rule == "to_utc" and status == "flagged" and detail.get("ambiguous"):
            found["DEF-05"].add(e["key"])
        elif rule == "map_result_code" and status == "flagged" and "sentinel" in e["note"]:
            found["DEF-08"].add(e["key"])
        elif rule == "map_assay" and status == "flagged" and detail.get("gap"):
            found["DEF-01"].add(e["key"])

    for q in quarantine:
        if q["quarantine_reason"] == "QTN-01":
            found["DEF-06"].add(q["key"])
        elif q["quarantine_reason"] == "QTN-02":
            found["DEF-07"].add(q["key"])

    for f in findings:
        if f["control"] == "REC-C01":
            found["DEF-09"].add("MANIFEST")

    return found


def score(oracle, found, quarantined_keys):
    """
    Two measures per defect, because they answer different questions.

    detected  the defect's own control fired on that row. This is the measure of
              whether the control works.

    contained the row did not reach the target unaltered -- either its control
              fired, or the row was quarantined by an earlier control in the
              chain. This is the measure of whether bad data got through.

    They differ where a row carries several defects: a result belonging to a
    duplicated donation is quarantined on the key before its assay code is ever
    examined, so the assay-mapping control never fires. Reporting only 'detected'
    would call that a miss; reporting only 'contained' would hide a control that
    does not work. A pack needs both numbers, so both are reported.
    """
    injected = defaultdict(set)
    for entry in oracle["injected"]:
        injected[entry["defect_id"]].add(entry["key"])

    rows = []
    for defect in DEFECT_CATALOGUE:
        want = injected.get(defect.id, set())
        got = found.get(defect.id, set())

        hit = want & got
        contained = hit | (want & quarantined_keys)
        uncontained = want - contained

        rows.append({
            "defect_id": defect.id,
            "name": defect.name,
            "dimension": defect.dimension,
            "control": defect.detected_by.split()[0],
            "injected": len(want),
            "detected": len(hit),
            "contained": len(contained),
            "detection_rate": round(len(hit) / len(want), 4) if want else 0.0,
            "containment_rate": round(len(contained) / len(want), 4) if want else 0.0,
            "result": "PASS" if want and not uncontained else
                      ("PARTIAL" if contained else "FAIL"),
            "uncontained_examples": sorted(uncontained)[:5],
        })
    return rows


def main():
    ap = argparse.ArgumentParser(description="Known-answer verification")
    ap.add_argument("--oracle", default="../data/_oracle", type=Path)
    ap.add_argument("--target", default="../data/target", type=Path)
    ap.add_argument("--evidence", default="../evidence", type=Path)
    args = ap.parse_args()

    oracle = json.loads((args.oracle / "injected_defects.json").read_text(encoding="utf-8"))
    audit = load_jsonl(args.target / "audit.jsonl")
    quarantine = load_jsonl(args.target / "quarantine.jsonl")
    summary = json.loads((args.target / "run_summary.json").read_text(encoding="utf-8"))

    quarantined_keys = {q["key"] for q in quarantine}
    rows = score(oracle, detected_keys(audit, quarantine, summary["findings"]),
                 quarantined_keys)

    print("=" * 88)
    print("OQ-KAT   Known-answer verification -- detected versus injected")
    print("=" * 88)
    print(f"  Oracle seed  {oracle['seed']}")
    print(f"  Run          {summary['run_id']}")
    print()
    w = max(len(r["name"]) for r in rows)
    print(f"  {'DEFECT':<9} {'NAME':<{w}}  {'CONTROL':<9} {'INJ':>6} {'DETECT':>7} {'CONTAIN':>8}  RESULT")
    print("  " + "-" * (9 + w + 46))
    for r in rows:
        print(f"  {r['defect_id']:<9} {r['name']:<{w}}  {r['control']:<9} "
              f"{r['injected']:>6} {r['detected']:>7} {r['contained']:>8}  {r['result']}")

    passed = sum(1 for r in rows if r["result"] == "PASS")
    total_inj = sum(r["injected"] for r in rows)
    total_det = sum(r["detected"] for r in rows)
    total_con = sum(r["contained"] for r in rows)
    print()
    print(f"  {passed}/{len(rows)} defects fully contained")
    print(f"  {total_det:,}/{total_inj:,} occurrences caught by their own control "
          f"({total_det / total_inj * 100:.1f}%)")
    print(f"  {total_con:,}/{total_inj:,} contained once rows quarantined earlier "
          f"in the chain are counted ({total_con / total_inj * 100:.1f}%)")

    failures = [r for r in rows if r["result"] != "PASS"]
    if failures:
        print("\n  Reached the target uncontained:")
        for r in failures:
            print(f"    {r['defect_id']}  {r['contained']}/{r['injected']}  "
                  f"e.g. {', '.join(r['uncontained_examples']) or '-'}")

    args.evidence.mkdir(parents=True, exist_ok=True)
    record = {
        "protocol": "OQ-KAT",
        "title": "Known-answer verification of migration controls",
        "executed_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "oracle_seed": oracle["seed"],
        "migration_run": summary["run_id"],
        "method": "The oracle of injected defects is written by the extract "
                  "generator and never read by the migration code. Detection is "
                  "attributed to defects on the scoring side, from the control "
                  "that fired, so the migration cannot be tuned to the answer.",
        "defects": rows,
        "summary": {
            "defects_total": len(rows),
            "defects_fully_detected": passed,
            "occurrences_injected": total_inj,
            "occurrences_detected": total_det,
            "occurrences_contained": sum(r["contained"] for r in rows),
        },
        "overall": "PASS" if passed == len(rows) else "FAIL",
    }
    out = args.evidence / "OQ-KAT-known-answer.json"
    out.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"\n  Evidence written to {out}")

    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
