"""
Verifies that the deployer credentials grant exactly the access the specification
calls for -- no more, no less. First installation qualification check.

Two independent instruments, because neither alone is sufficient:

  Part A -- live reachability probes. Real API calls proving the credential is
            usable end to end: signed correctly, region reachable, service
            responding. Positive cases only.

  Part B -- IAM policy simulation. Evaluates the authorisation decision without
            calling any service, so the verdict cannot be confounded by a
            missing resource, an unactivated service, or a service that returns
            its own error before IAM is consulted.

Part B exists because the first version of this script tested denials by making
real calls against non-existent resources. Those probes reported PASS on an
inconclusive result: S3 answered NoSuchBucket, which is indistinguishable from a
denial at the client. A negative control that cannot fail is not a control.

The simulator also separates an EXPLICIT deny (a guard rail fired) from an
IMPLICIT deny (the permission was simply never granted). Both refuse the call,
but only the first survives someone later attaching a broad Allow policy.

Every operation here is read-only. Nothing is created and nothing is billable.

Usage:
    python infra/provision/verify_access.py --profile gxp-val --region eu-west-1
"""

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError, NoCredentialsError, ProfileNotFound

PREFIX = "gxp-val"

AUTH_ERRORS = {
    "AccessDenied", "AccessDeniedException", "UnauthorizedOperation",
    "AuthorizationError", "NotAuthorized", "AccessDeniedError",
}


# ---------------------------------------------------------------------------
# Part A -- live reachability
# ---------------------------------------------------------------------------
def live_probes(session, region):
    s3 = session.client("s3", region_name=region)
    glue = session.client("glue", region_name=region)
    athena = session.client("athena", region_name=region)
    logs = session.client("logs", region_name=region)
    lam = session.client("lambda", region_name=region)

    return [
        ("IQ-AUTH-A01", "s3 reachable within gxp-val-* namespace",
         lambda: s3.list_objects_v2(Bucket=f"{PREFIX}-probe-nonexistent", MaxKeys=1)),
        ("IQ-AUTH-A02", "glue Data Catalog readable",
         lambda: glue.get_databases(MaxResults=1)),
        ("IQ-AUTH-A03", "athena workgroups listable",
         lambda: athena.list_work_groups(MaxResults=1)),
        ("IQ-AUTH-A04", "cloudwatch logs readable",
         lambda: logs.describe_log_groups(limit=1)),
        ("IQ-AUTH-A05", "lambda reachable within gxp-val-* namespace",
         lambda: lam.get_function(FunctionName=f"{PREFIX}-probe-nonexistent")),
    ]


def run_live(probe):
    probe_id, description, call = probe
    try:
        call()
        return _live_row(probe_id, description, "reachable", "call succeeded")
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in AUTH_ERRORS:
            return _live_row(probe_id, description, "refused", code)
        # Any non-authorisation error proves the call cleared the auth layer.
        return _live_row(probe_id, description, "reachable",
                         f"reached service ({code or 'non-auth error'})")
    except Exception as exc:                                    # noqa: BLE001
        return _live_row(probe_id, description, "error",
                         f"{type(exc).__name__}: {exc}")


def _live_row(probe_id, description, actual, detail):
    return {
        "probe_id": probe_id, "description": description,
        "expected": "reachable", "actual": actual, "detail": detail,
        "result": "PASS" if actual == "reachable" else "FAIL",
    }


# ---------------------------------------------------------------------------
# Part B -- policy simulation matrix
# ---------------------------------------------------------------------------
def simulation_matrix(account, region):
    """(probe_id, action, resource_arn, expected_decision, rationale)"""
    ours_bucket = f"arn:aws:s3:::{PREFIX}-data"
    other_bucket = "arn:aws:s3:::finance-production-reports"
    ours_fn = f"arn:aws:lambda:{region}:{account}:function:{PREFIX}-migrate"
    other_fn = f"arn:aws:lambda:{region}:{account}:function:payroll-processor"

    return [
        # --- must be permitted -------------------------------------------
        ("IQ-AUTH-B01", "s3:PutObject", f"{ours_bucket}/raw/x.txt", "allowed",
         "migration must write to its own bucket"),
        ("IQ-AUTH-B02", "s3:GetObject", f"{ours_bucket}/raw/x.txt", "allowed",
         "reconciliation must read it back"),
        ("IQ-AUTH-B03", "glue:CreateTable", "*", "allowed",
         "target tables are registered in the Data Catalog"),
        ("IQ-AUTH-B04", "athena:StartQueryExecution", "*", "allowed",
         "reconciliation controls run as Athena SQL"),
        ("IQ-AUTH-B05", "lambda:CreateFunction", ours_fn, "allowed",
         "the migration function is deployed by this principal"),
        ("IQ-AUTH-B06", "logs:DescribeLogGroups", "*", "allowed",
         "execution evidence is read from CloudWatch"),

        # --- must be EXPLICITLY denied (cost guard rails) ------------------
        ("IQ-AUTH-B07", "glue:CreateCrawler", "*", "explicitDeny",
         "crawlers bill 0.44 USD/DPU-hour with a 10-minute minimum"),
        ("IQ-AUTH-B08", "glue:StartJobRun", "*", "explicitDeny",
         "Glue ETL jobs are billable compute"),
        ("IQ-AUTH-B09", "redshift:CreateCluster", "*", "explicitDeny",
         "no free tier; Athena is used instead"),
        ("IQ-AUTH-B10", "kinesis:CreateStream", "*", "explicitDeny",
         "shard-hour billing, not required by this pipeline"),
        ("IQ-AUTH-B11", "sagemaker:CreateNotebookInstance", "*", "explicitDeny",
         "instance-hour billing, out of scope"),
        ("IQ-AUTH-B12", "ec2:RunInstances", "*", "explicitDeny",
         "no compute instances in this architecture"),
        ("IQ-AUTH-B13", "rds:CreateDBInstance", "*", "explicitDeny",
         "no managed database in this architecture"),

        # --- must be denied by absence of grant (blast radius) -------------
        ("IQ-AUTH-B14", "s3:PutObject", f"{other_bucket}/x.txt", "implicitDeny",
         "principal must not reach buckets outside its namespace"),
        ("IQ-AUTH-B15", "s3:DeleteObject", f"{other_bucket}/x.txt", "implicitDeny",
         "no delete outside its namespace"),
        ("IQ-AUTH-B16", "lambda:UpdateFunctionCode", other_fn, "implicitDeny",
         "principal must not alter unrelated functions"),
        ("IQ-AUTH-B17", "iam:CreateUser", "*", "implicitDeny",
         "principal must not create identities"),
        ("IQ-AUTH-B18", "iam:AttachUserPolicy", "*", "implicitDeny",
         "principal must not escalate its own privilege"),
    ]


def run_simulation(iam, principal_arn, matrix):
    rows = []
    for probe_id, action, resource, expected, rationale in matrix:
        try:
            resp = iam.simulate_principal_policy(
                PolicySourceArn=principal_arn,
                ActionNames=[action],
                ResourceArns=[resource],
            )
            decision = resp["EvaluationResults"][0]["EvalDecision"]
            detail = ""
        except ClientError as exc:
            decision = "simulation-error"
            detail = exc.response.get("Error", {}).get("Code", str(exc))

        # An explicit deny also satisfies an expectation of "denied in general",
        # but we assert the stronger, specific decision on purpose.
        rows.append({
            "probe_id": probe_id, "action": action, "resource": resource,
            "expected": expected, "actual": decision, "rationale": rationale,
            "detail": detail,
            "result": "PASS" if decision == expected else "FAIL",
        })
    return rows


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Verify deployer credential scope")
    ap.add_argument("--profile", default="gxp-val")
    ap.add_argument("--region", default="eu-west-1")
    ap.add_argument("--evidence", default="evidence", type=Path)
    args = ap.parse_args()

    try:
        session = boto3.Session(profile_name=args.profile)
    except ProfileNotFound:
        print(f"ERROR: AWS profile '{args.profile}' not found.")
        print(f"Run:  aws configure --profile {args.profile}")
        return 2

    try:
        identity = session.client("sts", region_name=args.region).get_caller_identity()
    except (NoCredentialsError, ClientError) as exc:
        print(f"ERROR: authentication failed for profile '{args.profile}': {exc}")
        return 2

    account, principal = identity["Account"], identity["Arn"]
    executed = dt.datetime.now(dt.timezone.utc)

    print("=" * 76)
    print("IQ-AUTH   Deployer credential scope verification")
    print("=" * 76)
    print(f"  Account   : {account}")
    print(f"  Principal : {principal}")
    print(f"  Region    : {args.region}")
    print(f"  Executed  : {executed.isoformat()}")

    # ---- Part A ----------------------------------------------------------
    print("\n  Part A -- live reachability (real API calls)")
    print("  " + "-" * 72)
    live = [run_live(p) for p in live_probes(session, args.region)]
    w = max(len(r["description"]) for r in live)
    for r in live:
        print(f"  {r['probe_id']:<13} {r['description']:<{w}}  {r['actual']:<10} {r['result']}")

    # ---- Part B ----------------------------------------------------------
    print("\n  Part B -- IAM policy simulation (authorisation decision only)")
    print("  " + "-" * 72)
    iam = session.client("iam", region_name=args.region)
    matrix = simulation_matrix(account, args.region)
    sims = run_simulation(iam, principal, matrix)

    wa = max(len(r["action"]) for r in sims)
    print(f"  {'PROBE':<13} {'ACTION':<{wa}}  {'EXPECTED':<14} {'ACTUAL':<14} RESULT")
    for r in sims:
        print(f"  {r['probe_id']:<13} {r['action']:<{wa}}  "
              f"{r['expected']:<14} {r['actual']:<14} {r['result']}")

    # ---- verdict ---------------------------------------------------------
    allrows = live + sims
    passed = sum(1 for r in allrows if r["result"] == "PASS")
    failed = len(allrows) - passed

    print("\n  " + "=" * 72)
    print(f"  {passed}/{len(allrows)} passed" + (f", {failed} FAILED" if failed else ""))
    if failed:
        print("\n  Failures:")
        for r in allrows:
            if r["result"] == "FAIL":
                label = r.get("description") or f"{r['action']} on {r['resource']}"
                print(f"    {r['probe_id']}  {label}")
                print(f"       expected {r['expected']}, got {r['actual']} "
                      f"{('(' + r['detail'] + ')') if r.get('detail') else ''}")

    args.evidence.mkdir(parents=True, exist_ok=True)
    record = {
        "protocol": "IQ-AUTH",
        "title": "Deployer credential scope verification",
        "executed_utc": executed.isoformat(),
        "aws_account": account,
        "principal_arn": principal,
        "region": args.region,
        "part_a_live_reachability": live,
        "part_b_policy_simulation": sims,
        "summary": {"total": len(allrows), "passed": passed, "failed": failed},
        "overall": "PASS" if failed == 0 else "FAIL",
    }
    out = args.evidence / "IQ-AUTH-credential-scope.json"
    out.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"\n  Evidence written to {out}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
