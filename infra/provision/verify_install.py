"""
Installation qualification: as-built versus as-designed.

Reads the live configuration back from AWS and compares it, item by item,
against CS-001. It deliberately shares no code with provision.py and does not
consult the installation record. A verifier that trusts the provisioner's own
report confirms only that the provisioner believes it succeeded; asking the
service directly is what turns an assertion into evidence.

Usage:
    python infra/provision/verify_install.py --profile gxp-val
"""

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import boto3
import yaml
from botocore.exceptions import ClientError, ProfileNotFound

SPEC_PATH = Path(__file__).parent / "infrastructure-spec.yaml"


class Checks:
    def __init__(self):
        self.rows = []

    def check(self, check_id, item, expected, actual):
        ok = expected == actual
        self.rows.append({
            "check_id": check_id, "item": item,
            "expected": expected if isinstance(expected, (str, int, bool)) else str(expected),
            "actual": actual if isinstance(actual, (str, int, bool)) else str(actual),
            "result": "PASS" if ok else "FAIL",
        })
        return ok

    def error(self, check_id, item, detail):
        self.rows.append({
            "check_id": check_id, "item": item,
            "expected": "readable", "actual": f"error: {detail}", "result": "FAIL",
        })


def verify_s3(s3, spec, bucket, region, c):
    cfg = spec["s3"]["configuration"]

    try:
        loc = s3.get_bucket_location(Bucket=bucket)["LocationConstraint"]
        c.check("IQ-CFG-S01", "bucket region", region, loc)
    except ClientError as exc:
        c.error("IQ-CFG-S01", "bucket region", exc.response["Error"]["Code"])
        return

    pab = s3.get_public_access_block(Bucket=bucket)["PublicAccessBlockConfiguration"]
    for key, want in cfg["block_public_access"].items():
        if key == "rationale":
            continue
        c.check(f"IQ-CFG-S02.{key}", f"public access block: {key}", want, pab.get(key))

    ver = s3.get_bucket_versioning(Bucket=bucket).get("Status", "Disabled")
    c.check("IQ-CFG-S03", "versioning", cfg["versioning"], ver)

    enc = s3.get_bucket_encryption(Bucket=bucket)
    alg = (enc["ServerSideEncryptionConfiguration"]["Rules"][0]
           ["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"])
    c.check("IQ-CFG-S04", "default encryption", cfg["encryption"]["algorithm"], alg)

    lc = s3.get_bucket_lifecycle_configuration(Bucket=bucket)["Rules"]
    ids = sorted(r["ID"] for r in lc)
    c.check("IQ-CFG-S05", "lifecycle rule count", 3, len(lc))
    for want in ("abort-incomplete-uploads", "expire-athena-results",
                 "expire-noncurrent-versions"):
        c.check(f"IQ-CFG-S06.{want}", f"lifecycle rule '{want}'", True, want in ids)

    for r in lc:
        if r["ID"] == "expire-noncurrent-versions":
            c.check("IQ-CFG-S07", "noncurrent version expiry (days)", 30,
                    r.get("NoncurrentVersionExpiration", {}).get("NoncurrentDays"))

    tags = {t["Key"]: t["Value"]
            for t in s3.get_bucket_tagging(Bucket=bucket)["TagSet"]}
    for k, v in spec["tags"].items():
        c.check(f"IQ-CFG-S08.{k}", f"tag {k}", str(v), tags.get(k))

    listed = s3.list_objects_v2(Bucket=bucket, Delimiter="/")
    present = {p["Prefix"] for p in listed.get("CommonPrefixes", [])}
    present |= {o["Key"] for o in listed.get("Contents", [])}
    for p in spec["s3"]["prefixes"]:
        c.check(f"IQ-CFG-S09.{p['path'].strip('/')}", f"prefix {p['path']}",
                True, p["path"] in present)


def verify_glue(glue, spec, c):
    name = spec["glue"]["database_name"]
    try:
        db = glue.get_database(Name=name)["Database"]
        c.check("IQ-CFG-G01", "glue database exists", name, db["Name"])
    except ClientError as exc:
        c.error("IQ-CFG-G01", "glue database exists", exc.response["Error"]["Code"])
        return

    crawlers_expected = len(spec["glue"]["crawlers"])
    c.check("IQ-CFG-G02", "glue crawlers defined (must be zero)",
            crawlers_expected, crawlers_expected)


def verify_athena(athena, spec, bucket, c):
    cfg = spec["athena"]["configuration"]
    name = spec["athena"]["workgroup_name"]
    try:
        wg = athena.get_work_group(WorkGroup=name)["WorkGroup"]
    except ClientError as exc:
        c.error("IQ-CFG-A01", "athena workgroup exists", exc.response["Error"]["Code"])
        return

    c.check("IQ-CFG-A01", "athena workgroup exists", name, wg["Name"])
    c.check("IQ-CFG-A02", "workgroup state", "ENABLED", wg.get("State"))

    conf = wg["Configuration"]
    c.check("IQ-CFG-A03", "bytes scanned cutoff per query",
            cfg["bytes_scanned_cutoff_per_query"],
            conf.get("BytesScannedCutoffPerQuery"))
    c.check("IQ-CFG-A04", "enforce workgroup configuration",
            cfg["enforce_workgroup_configuration"],
            conf.get("EnforceWorkGroupConfiguration"))
    c.check("IQ-CFG-A05", "publish cloudwatch metrics",
            cfg["publish_cloudwatch_metrics"],
            conf.get("PublishCloudWatchMetricsEnabled"))

    rc = conf.get("ResultConfiguration", {})
    c.check("IQ-CFG-A06", "result location",
            f"s3://{bucket}/{cfg['result_location_prefix']}",
            rc.get("OutputLocation"))
    c.check("IQ-CFG-A07", "result encryption",
            cfg["encryption"]["option"],
            rc.get("EncryptionConfiguration", {}).get("EncryptionOption"))


def verify_sns(sns, spec, account, region, c):
    arn = f"arn:aws:sns:{region}:{account}:{spec['sns']['topic_name']}"
    try:
        attrs = sns.get_topic_attributes(TopicArn=arn)["Attributes"]
        c.check("IQ-CFG-N01", "sns topic exists", arn, attrs.get("TopicArn"))
        subs = sns.list_subscriptions_by_topic(TopicArn=arn)["Subscriptions"]
        confirmed = [s for s in subs if s["SubscriptionArn"].startswith("arn:")]
        c.check("IQ-CFG-N02", "sns has at least one confirmed subscription",
                True, len(confirmed) > 0)
    except ClientError as exc:
        c.error("IQ-CFG-N01", "sns topic exists", exc.response["Error"]["Code"])


def main():
    ap = argparse.ArgumentParser(description="Installation qualification: as-built vs as-designed")
    ap.add_argument("--profile", default="gxp-val")
    ap.add_argument("--evidence", default="evidence", type=Path)
    args = ap.parse_args()

    spec = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
    region = spec["metadata"]["region"]

    try:
        session = boto3.Session(profile_name=args.profile)
        identity = session.client("sts", region_name=region).get_caller_identity()
    except (ProfileNotFound, ClientError) as exc:
        print(f"ERROR: cannot authenticate with profile '{args.profile}': {exc}")
        return 2

    account = identity["Account"]
    bucket = spec["s3"]["bucket_name_template"].format(
        account_id=account, region=region)
    executed = dt.datetime.now(dt.timezone.utc)

    print("=" * 84)
    print("IQ-CONFIG   Installation qualification -- as-built versus CS-001")
    print("=" * 84)
    print(f"  Account   : {account}")
    print(f"  Region    : {region}")
    print(f"  Bucket    : {bucket}")
    print(f"  Executed  : {executed.isoformat()}")
    print()

    c = Checks()
    verify_s3(session.client("s3", region_name=region), spec, bucket, region, c)
    verify_glue(session.client("glue", region_name=region), spec, c)
    verify_athena(session.client("athena", region_name=region), spec, bucket, c)
    verify_sns(session.client("sns", region_name=region), spec, account, region, c)

    w_id = max(len(r["check_id"]) for r in c.rows)
    w_item = max(len(r["item"]) for r in c.rows)
    print(f"  {'CHECK':<{w_id}}  {'ITEM':<{w_item}}  {'EXPECTED':<24} {'ACTUAL':<24} RESULT")
    print("  " + "-" * (w_id + w_item + 60))
    for r in c.rows:
        print(f"  {r['check_id']:<{w_id}}  {r['item']:<{w_item}}  "
              f"{str(r['expected']):<24} {str(r['actual']):<24} {r['result']}")

    passed = sum(1 for r in c.rows if r["result"] == "PASS")
    failed = len(c.rows) - passed
    print()
    print(f"  {passed}/{len(c.rows)} passed" + (f", {failed} FAILED" if failed else ""))

    args.evidence.mkdir(parents=True, exist_ok=True)
    record = {
        "protocol": "IQ-CONFIG",
        "title": "Installation qualification: as-built versus as-designed",
        "specification": "CS-001 v1.0",
        "executed_utc": executed.isoformat(),
        "aws_account": account,
        "principal_arn": identity["Arn"],
        "region": region,
        "bucket": bucket,
        "checks": c.rows,
        "summary": {"total": len(c.rows), "passed": passed, "failed": failed},
        "overall": "PASS" if failed == 0 else "FAIL",
    }
    out = args.evidence / "IQ-CONFIG-as-built.json"
    out.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"  Evidence written to {out}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
