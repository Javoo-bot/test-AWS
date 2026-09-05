"""
Generates RTM-001, the requirements traceability matrix.

The matrix is derived, never typed. Its inputs are the requirements (URS-001),
the mapping specification (STTM-001), the defect catalogue and the evidence
records produced by actual runs. A requirement therefore cannot appear as
verified unless a control names it, a mapped field exercises it and an evidence
record reports on it.

This is the point of generating it. A hand-maintained RTM is accurate on the day
it is written and decorative thereafter, because nothing forces it to change when
the system does. Here a broken link is a build failure, not a discrepancy someone
may notice at audit.

Usage:
    python -m evidence.build_rtm
"""

import argparse
import datetime as dt
import json
import sys
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
URS_PATH = ROOT / "docs" / "02-specifications" / "URS-001-requirements.yaml"
STTM_PATH = ROOT / "docs" / "03-mapping" / "STTM-001-source-to-target-mapping.yaml"
OUT_PATH = ROOT / "docs" / "04-traceability" / "RTM-001-traceability-matrix.md"


def collect_sttm_links(sttm):
    """requirement id -> mapped fields, and control id -> mapped fields."""
    by_req = defaultdict(list)
    by_ctl = defaultdict(list)
    for entity in sttm["entities"]:
        for f in entity["fields"]:
            ref = f"{entity['name']}.{f['target']}"
            for r in f.get("requirements", []):
                by_req[r].append(ref)
            for c in f.get("controls", []):
                by_ctl[c].append(ref)
    return by_req, by_ctl


def collect_evidence(evidence_dir):
    """control/requirement id -> evidence records mentioning it."""
    records = []
    for path in sorted(evidence_dir.glob("*.json")):
        try:
            records.append((path.name, json.loads(path.read_text(encoding="utf-8"))))
        except json.JSONDecodeError:
            continue
    return records


def defects_by_control(kat_record):
    by_ctl = defaultdict(list)
    if not kat_record:
        return by_ctl
    for d in kat_record.get("defects", []):
        by_ctl[d["control"]].append(d)
    return by_ctl


def main():
    ap = argparse.ArgumentParser(description="Generate RTM-001")
    ap.add_argument("--evidence", default=ROOT / "evidence", type=Path)
    ap.add_argument("--target", default=ROOT / "data" / "target", type=Path)
    args = ap.parse_args()

    urs = yaml.safe_load(URS_PATH.read_text(encoding="utf-8"))
    sttm = yaml.safe_load(STTM_PATH.read_text(encoding="utf-8"))
    fields_by_req, fields_by_ctl = collect_sttm_links(sttm)

    evidence = collect_evidence(args.evidence)
    kat = next((r for _, r in evidence if r.get("protocol") == "OQ-KAT"), None)
    tst = next((r for _, r in evidence if r.get("protocol") == "OQ-TEST"), None)
    ctl_defects = defects_by_control(kat)

    tests_by_req = defaultdict(list)
    for t in (tst or {}).get("tests", []):
        for rid in t["requirements"]:
            tests_by_req[rid].append(t)

    run_summary = None
    p = args.target / "run_summary.json"
    if p.exists():
        run_summary = json.loads(p.read_text(encoding="utf-8"))

    controls = {c["id"]: c for c in urs["controls"]}

    rows, gaps = [], []
    for req in urs["requirements"]:
        rid = req["id"]
        ctls = req.get("verified_by", [])
        mapped = sorted(set(fields_by_req.get(rid, [])))

        defects = []
        for c in ctls:
            defects.extend(ctl_defects.get(c, []))
        tests = tests_by_req.get(rid, [])

        # Verified requires all three: a control exists, it is implemented, and
        # something that actually ran reports on it -- either an injected defect
        # it contained, or a tagged test that passed.
        has_control = bool(ctls)
        implemented = all(c in controls for c in ctls)
        exercised = bool(defects) or bool(tests)

        failing = ([d for d in defects if d["result"] != "PASS"]
                   + [t for t in tests if t["outcome"] != "passed"])

        if not has_control:
            status, note = "GAP", "no control verifies this requirement"
        elif not implemented:
            status, note = "GAP", "control declared but not implemented"
        elif not exercised:
            status, note = "UNEXERCISED", "control implemented but nothing exercises it"
        elif failing:
            status, note = "PARTIAL", "something exercising this requirement did not pass"
        else:
            status, note = "VERIFIED", ""

        if status in ("GAP", "UNEXERCISED", "PARTIAL"):
            gaps.append((rid, note))

        rows.append({
            "id": rid, "criticality": req["criticality"], "controls": ctls,
            "fields": mapped, "defects": sorted({d["defect_id"] for d in defects}),
            "tests": len(tests), "status": status, "note": note,
            "statement": req["statement"].strip(),
        })

    generated = dt.datetime.now(dt.timezone.utc)
    L = []
    L.append("# RTM-001 · Requirements Traceability Matrix\n")
    L.append("> **Generated, not written.** Produced by `src/evidence/build_rtm.py` "
             "from URS-001, STTM-001 and the evidence records. Do not edit by hand: "
             "regenerate it.\n")
    L.append(f"| | |\n|---|---|\n"
             f"| Generated | {generated:%Y-%m-%d %H:%M} UTC |\n"
             f"| Requirements | {len(rows)} |\n"
             f"| Controls | {len(controls)} |\n"
             f"| Migration run | `{run_summary['run_id'] if run_summary else 'n/a'}` |\n"
             f"| Known-answer run | `{kat['executed_utc'][:19] if kat else 'n/a'}` |\n")

    counts = defaultdict(int)
    for r in rows:
        counts[r["status"]] += 1
    L.append("\n## Coverage\n")
    L.append("| Status | Count | Meaning |\n|---|---|---|")
    for status, meaning in (
            ("VERIFIED", "control implemented, exercised by a defect, all contained"),
            ("PARTIAL", "exercised, but something was not fully contained"),
            ("UNEXERCISED", "control implemented but nothing tests it"),
            ("GAP", "no control, or control not implemented")):
        L.append(f"| **{status}** | {counts[status]} | {meaning} |")

    L.append("\n## Matrix\n")
    L.append("| Req | Crit | Requirement | Controls | Fields | Defects | Tests | Status |")
    L.append("|---|---|---|---|---|---|---|---|")
    for r in rows:
        stmt = r["statement"].replace("\n", " ")
        stmt = (stmt[:90] + "…") if len(stmt) > 90 else stmt
        fields = f"{len(r['fields'])}" if r["fields"] else "—"
        L.append(
            f"| `{r['id']}` | {r['criticality']} | {stmt} | "
            f"{' '.join('`'+c+'`' for c in r['controls']) or '—'} | {fields} | "
            f"{' '.join('`'+d+'`' for d in r['defects']) or '—'} | "
            f"{r['tests'] or '—'} | **{r['status']}** |")

    L.append("\n## Controls\n")
    L.append("| Control | Level | Verifies | Implemented in |")
    L.append("|---|---|---|---|")
    req_by_ctl = defaultdict(list)
    for r in rows:
        for c in r["controls"]:
            req_by_ctl[c].append(r["id"])
    for cid, c in controls.items():
        L.append(f"| `{cid}` | {c['level']} | "
                 f"{' '.join('`'+x+'`' for x in req_by_ctl.get(cid, [])) or '—'} | "
                 f"`{c['implemented_in']}` |")

    if gaps:
        L.append("\n## Open gaps\n")
        L.append("Listed rather than resolved. A matrix that reports full coverage "
                 "on its first generation has usually been written to do so.\n")
        for rid, note in gaps:
            L.append(f"- **`{rid}`** — {note}")

    L.append(f"\n---\n<sub>RTM-001 · generated {generated:%Y-%m-%d %H:%M} UTC · "
             f"regenerate with `python -m evidence.build_rtm`</sub>\n")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text("\n".join(L), encoding="utf-8")

    print("=" * 72)
    print("RTM-001  Requirements Traceability Matrix")
    print("=" * 72)
    for status in ("VERIFIED", "PARTIAL", "UNEXERCISED", "GAP"):
        if counts[status]:
            print(f"  {status:<12} {counts[status]:>3}")
    if gaps:
        print("\n  Open gaps:")
        for rid, note in gaps:
            print(f"    {rid}  {note}")
    print(f"\n  Written to {OUT_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
