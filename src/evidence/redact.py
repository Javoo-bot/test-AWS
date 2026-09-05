"""
Redacts account identifiers from evidence records before publication.

Data minimisation applied to the evidence itself. The records stay complete --
every check, verdict and timestamp is intact -- but the AWS account identifier
is replaced by a stable placeholder, because it is an identifier of the
environment and not a fact about whether the checks passed.

The redaction is recorded inside each file rather than performed invisibly.

Usage:
    python src/evidence/redact.py --profile gxp-val
"""

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import boto3

PLACEHOLDER = "<AWS_ACCOUNT_ID>"


def main():
    ap = argparse.ArgumentParser(description="Redact account identifiers from evidence")
    ap.add_argument("--profile", default="gxp-val")
    ap.add_argument("--evidence", default="evidence", type=Path)
    args = ap.parse_args()

    account = (boto3.Session(profile_name=args.profile)
               .client("sts", region_name="eu-west-1")
               .get_caller_identity()["Account"])

    files = sorted(args.evidence.glob("*.json"))
    if not files:
        print(f"No evidence files under {args.evidence}")
        return 1

    changed = 0
    for path in files:
        text = path.read_text(encoding="utf-8")
        hits = text.count(account)
        if not hits:
            print(f"  clean     {path.name}")
            continue

        text = text.replace(account, PLACEHOLDER)
        record = json.loads(text)
        record["redaction"] = {
            "applied_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "fields": f"AWS account identifier replaced with {PLACEHOLDER}",
            "occurrences": hits,
            "rationale": "Data minimisation for publication. No check result, "
                         "verdict or timestamp is altered.",
        }
        path.write_text(json.dumps(record, indent=2), encoding="utf-8")
        print(f"  redacted  {path.name}  ({hits} occurrences)")
        changed += 1

    print(f"\n{changed} of {len(files)} evidence files redacted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
