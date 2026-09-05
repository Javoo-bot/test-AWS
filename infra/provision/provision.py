"""
Provisions the validation environment from CS-001.

Every action is idempotent and every action is recorded. The installation record
this writes is the raw material for the installation qualification: what was
created, when, by which principal, and with what configuration.

Usage:
    python infra/provision/provision.py --profile gxp-val
    python infra/provision/provision.py --profile gxp-val --alert-email you@example.com
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


class Recorder:
    """Accumulates a timestamped record of every provisioning action."""

    def __init__(self):
        self.actions = []

    def record(self, resource, action, outcome, detail=""):
        entry = {
            "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "resource": resource,
            "action": action,
            "outcome": outcome,          # created | already_present | skipped | failed
            "detail": detail,
        }
        self.actions.append(entry)
        symbol = {"created": "+", "already_present": "=",
                  "skipped": ".", "failed": "!"}.get(outcome, "?")
        print(f"  {symbol} {resource:<34} {outcome:<16} {detail}")
        return entry


def tag_list(tags):
    return [{"Key": k, "Value": str(v)} for k, v in tags.items()]


# ---------------------------------------------------------------------------
def provision_s3(session, spec, bucket, region, rec):
    s3 = session.client("s3", region_name=region)
    cfg = spec["s3"]["configuration"]

    try:
        s3.head_bucket(Bucket=bucket)
        rec.record(f"s3://{bucket}", "create_bucket", "already_present")
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in ("404", "NoSuchBucket"):
            rec.record(f"s3://{bucket}", "create_bucket", "failed",
                       exc.response["Error"]["Code"])
            return
        s3.create_bucket(
            Bucket=bucket,
            CreateBucketConfiguration={"LocationConstraint": region},
        )
        rec.record(f"s3://{bucket}", "create_bucket", "created", region)

    s3.put_public_access_block(
        Bucket=bucket,
        PublicAccessBlockConfiguration={
            k: v for k, v in cfg["block_public_access"].items()
            if k != "rationale"
        },
    )
    rec.record(f"s3://{bucket}", "put_public_access_block", "created", "all four blocks on")

    s3.put_bucket_versioning(
        Bucket=bucket, VersioningConfiguration={"Status": cfg["versioning"]})
    rec.record(f"s3://{bucket}", "put_bucket_versioning", "created", cfg["versioning"])

    s3.put_bucket_encryption(
        Bucket=bucket,
        ServerSideEncryptionConfiguration={
            "Rules": [{
                "ApplyServerSideEncryptionByDefault": {
                    "SSEAlgorithm": cfg["encryption"]["algorithm"]},
                "BucketKeyEnabled": True,
            }]
        },
    )
    rec.record(f"s3://{bucket}", "put_bucket_encryption", "created",
               cfg["encryption"]["algorithm"])

    s3.put_bucket_lifecycle_configuration(
        Bucket=bucket,
        LifecycleConfiguration={"Rules": [
            {
                "ID": "expire-noncurrent-versions", "Status": "Enabled",
                "Filter": {"Prefix": ""},
                "NoncurrentVersionExpiration": {"NoncurrentDays": 30},
            },
            {
                "ID": "abort-incomplete-uploads", "Status": "Enabled",
                "Filter": {"Prefix": ""},
                "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7},
            },
            {
                "ID": "expire-athena-results", "Status": "Enabled",
                "Filter": {"Prefix": "athena-results/"},
                "Expiration": {"Days": 7},
            },
        ]},
    )
    rec.record(f"s3://{bucket}", "put_lifecycle_configuration", "created", "3 rules")

    s3.put_bucket_tagging(Bucket=bucket,
                          Tagging={"TagSet": tag_list(spec["tags"])})
    rec.record(f"s3://{bucket}", "put_bucket_tagging", "created",
               f"{len(spec['tags'])} tags")

    for p in spec["s3"]["prefixes"]:
        s3.put_object(Bucket=bucket, Key=p["path"], Body=b"")
    rec.record(f"s3://{bucket}", "create_prefixes", "created",
               ", ".join(p["path"] for p in spec["s3"]["prefixes"]))


def provision_glue(session, spec, region, rec):
    glue = session.client("glue", region_name=region)
    name = spec["glue"]["database_name"]
    try:
        glue.get_database(Name=name)
        rec.record(f"glue:{name}", "create_database", "already_present")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "EntityNotFoundException":
            rec.record(f"glue:{name}", "create_database", "failed",
                       exc.response["Error"]["Code"])
            return
        glue.create_database(DatabaseInput={
            "Name": name, "Description": spec["glue"]["description"]})
        rec.record(f"glue:{name}", "create_database", "created")


def provision_athena(session, spec, bucket, region, rec):
    athena = session.client("athena", region_name=region)
    cfg = spec["athena"]["configuration"]
    name = spec["athena"]["workgroup_name"]
    output = f"s3://{bucket}/{cfg['result_location_prefix']}"

    wg_config = {
        "ResultConfiguration": {
            "OutputLocation": output,
            "EncryptionConfiguration": {
                "EncryptionOption": cfg["encryption"]["option"]},
        },
        "EnforceWorkGroupConfiguration": cfg["enforce_workgroup_configuration"],
        "PublishCloudWatchMetricsEnabled": cfg["publish_cloudwatch_metrics"],
        "BytesScannedCutoffPerQuery": cfg["bytes_scanned_cutoff_per_query"],
    }

    try:
        athena.get_work_group(WorkGroup=name)
        athena.update_work_group(
            WorkGroup=name,
            ConfigurationUpdates={
                "ResultConfigurationUpdates": {
                    "OutputLocation": output,
                    "EncryptionConfiguration": {
                        "EncryptionOption": cfg["encryption"]["option"]},
                },
                "EnforceWorkGroupConfiguration":
                    cfg["enforce_workgroup_configuration"],
                "PublishCloudWatchMetricsEnabled":
                    cfg["publish_cloudwatch_metrics"],
                "BytesScannedCutoffPerQuery":
                    cfg["bytes_scanned_cutoff_per_query"],
            },
        )
        rec.record(f"athena:{name}", "update_work_group", "already_present",
                   "configuration reapplied")
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in (
                "InvalidRequestException", "ResourceNotFoundException"):
            rec.record(f"athena:{name}", "create_work_group", "failed",
                       exc.response["Error"]["Code"])
            return
        # Tags are applied separately: CreateWorkGroup with Tags additionally
        # requires athena:TagResource, which the deployer policy does not grant.
        # Tagging is convenience, not control, so it is not worth widening the
        # principal's scope for. The omission is recorded rather than hidden.
        athena.create_work_group(
            Name=name, Configuration=wg_config,
            Description="Reconciliation controls for the LIS migration",
        )
        mb = cfg["bytes_scanned_cutoff_per_query"] // 1024 // 1024
        rec.record(f"athena:{name}", "create_work_group", "created",
                   f"scan cutoff {mb} MB, enforced")
        rec.record(f"athena:{name}", "tag_resource", "skipped",
                   "athena:TagResource not granted to deployer principal")


def provision_sns(session, spec, region, rec, alert_email=None):
    sns = session.client("sns", region_name=region)
    name = spec["sns"]["topic_name"]
    # As with Athena, Tags at creation would require sns:TagResource.
    resp = sns.create_topic(Name=name)
    arn = resp["TopicArn"]
    rec.record(f"sns:{name}", "create_topic", "created", arn)

    if alert_email:
        sns.subscribe(TopicArn=arn, Protocol="email", Endpoint=alert_email)
        rec.record(f"sns:{name}", "subscribe", "created",
                   f"{alert_email} -- CONFIRM VIA EMAIL")
    else:
        rec.record(f"sns:{name}", "subscribe", "skipped",
                   "no --alert-email given")
    return arn


def provision_budget(session, spec, account, rec, alert_email=None):
    if not alert_email:
        rec.record("budgets:gxp-val-cost-guard", "create_budget", "skipped",
                   "requires --alert-email")
        return
    budgets = session.client("budgets", region_name="us-east-1")
    b = spec["budgets"]
    subscribers = [{"SubscriptionType": "EMAIL", "Address": alert_email}]
    notifications = [
        {
            "Notification": {
                "NotificationType": "ACTUAL",
                "ComparisonOperator": "GREATER_THAN",
                "Threshold": float(t),
                "ThresholdType": "PERCENTAGE",
            },
            "Subscribers": subscribers,
        }
        for t in b["thresholds_percent"]
    ]
    try:
        budgets.create_budget(
            AccountId=account,
            Budget={
                "BudgetName": b["name"],
                "BudgetLimit": {"Amount": str(b["limit_usd"]), "Unit": "USD"},
                "TimeUnit": b["time_unit"],
                "BudgetType": "COST",
            },
            NotificationsWithSubscribers=notifications,
        )
        rec.record(f"budgets:{b['name']}", "create_budget", "created",
                   f"{b['limit_usd']} USD/month, alerts at "
                   f"{'/'.join(str(t) for t in b['thresholds_percent'])}%")
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        outcome = "already_present" if code == "DuplicateRecordException" else "failed"
        rec.record(f"budgets:{b['name']}", "create_budget", outcome, code)


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Provision the validation environment")
    ap.add_argument("--profile", default="gxp-val")
    ap.add_argument("--alert-email", default=None,
                    help="Email for SNS alerts and the cost budget")
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
    started = dt.datetime.now(dt.timezone.utc)

    print("=" * 76)
    print("Provisioning validation environment from CS-001")
    print("=" * 76)
    print(f"  Account   : {account}")
    print(f"  Principal : {identity['Arn']}")
    print(f"  Region    : {region}")
    print(f"  Bucket    : {bucket}")
    print(f"  Started   : {started.isoformat()}")
    print()

    rec = Recorder()
    provision_s3(session, spec, bucket, region, rec)
    provision_glue(session, spec, region, rec)
    provision_athena(session, spec, bucket, region, rec)
    topic_arn = provision_sns(session, spec, region, rec, args.alert_email)
    provision_budget(session, spec, account, rec, args.alert_email)

    finished = dt.datetime.now(dt.timezone.utc)
    failures = [a for a in rec.actions if a["outcome"] == "failed"]

    print()
    print(f"  {len(rec.actions)} actions, {len(failures)} failed, "
          f"{(finished - started).total_seconds():.1f}s")

    args.evidence.mkdir(parents=True, exist_ok=True)
    record = {
        "protocol": "IQ-INSTALL",
        "title": "Environment installation record",
        "specification": "CS-001 v1.0",
        "started_utc": started.isoformat(),
        "finished_utc": finished.isoformat(),
        "aws_account": account,
        "principal_arn": identity["Arn"],
        "region": region,
        "resources": {
            "bucket": bucket,
            "glue_database": spec["glue"]["database_name"],
            "athena_workgroup": spec["athena"]["workgroup_name"],
            "sns_topic_arn": topic_arn,
        },
        "actions": rec.actions,
        "overall": "PASS" if not failures else "FAIL",
    }
    out = args.evidence / "IQ-INSTALL-environment.json"
    out.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"  Installation record written to {out}")

    # Emit the resolved names for the .env file.
    print("\n  Add to .env:")
    print(f"    GXP_BUCKET={bucket}")
    print(f"    GXP_GLUE_DATABASE={spec['glue']['database_name']}")
    print(f"    GXP_ATHENA_WORKGROUP={spec['athena']['workgroup_name']}")
    print(f"    GXP_SNS_TOPIC_ARN={topic_arn}")

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
