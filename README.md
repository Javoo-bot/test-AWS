# Data Migration Validation Pack — Legacy LIS → AWS

A working validation package for a data migration in a GxP-regulated environment:
five years of historical **blood-donor screening records** moving from a legacy
Laboratory Information System being decommissioned into an AWS data platform.

It is deliberately two things at once. The documents are the validation half.
The code underneath is the technical half, and it runs — against real AWS, with
real execution evidence, at a cost of effectively zero.

---

## Why donor screening

Because the risk argument writes itself. In blood screening, the result of an
assay decides whether a unit of blood is **released for transfusion or
destroyed**. A field corrupted in migration is not a defect report, it is a
patient-safety event.

That makes the risk-based testing section defensible rather than theoretical:
test depth follows the consequence of getting a field wrong, and the consequence
here is not a matter of opinion.

---

## The part that matters

Nine data defects are **deliberately injected** into the source extract. A
validation pack in which every check passes has demonstrated nothing; what shows
competence is finding real problems and giving each one a documented disposition.

| ID | Defect | Why it is realistic |
|----|--------|---------------------|
| DEF-01 | Legacy assay codes with no target vocabulary equivalent | Every legacy LIS has proprietary codes |
| DEF-02 | Haemoglobin in g/dL vs g/L in target | Wrong by 10×, and looks perfectly well-formed |
| DEF-03 | Censored (`<0.10`), padded, comma-decimal numerics | Results held as free text |
| DEF-04 | Donor names double-encoded at rest before migration | Latin-1 / UTF-8 corruption that predates the project |
| DEF-05 | Local wall-clock timestamps, two offsets, DST fallback hour | Source schema has no offset column |
| DEF-06 | Duplicate donation identifiers | Artefact of a historical site-database merge |
| DEF-07 | Orphan results referencing absent donations | Extract windows not taken atomically |
| DEF-08 | Undocumented sentinel `9999` = "test not performed" | Absent from the legacy data dictionary |
| DEF-09 | Source manifest control total disagrees with the file | Count taken before a late row was flushed |

DEF-04 is the one worth pausing on. The corrupted rows are the ones that *look
correct* when read as UTF-8, while the clean rows look broken. That inversion is
why the defect survives visual inspection and reaches production — 6 of 500
donors, invisible to the eye, provable at the byte level.

---

## Architecture

```
S3 raw/ ──event──▶ Lambda (validate + transform to Parquet)
                     ├──▶ S3 curated/     partitioned by year
                     ├──▶ S3 quarantine/  rejected rows + the rule they broke
                     └──▶ S3 audit/       append-only decision trail
                           │
             Glue Data Catalog (DDL from STTM-001, no crawler)
                           │
                        Athena (reconciliation controls, ANSI SQL)

Cross-cutting: S3 versioning · CloudTrail management events · CloudWatch Logs
               SNS alerting · AWS Budgets tripwire
```

**Athena stands in for the client's SQL warehouse.** The reconciliation controls
are ANSI SQL and port to Snowflake or Redshift unchanged. That substitution is a
known limitation of a zero-cost demonstration environment, and it is recorded in
`CS-001` rather than left implicit.

**No Glue crawler**, on purpose. Crawlers bill at $0.44/DPU-hour with a 10-minute
minimum, and they *infer* schema rather than enforce it. Tables are created from
explicit DDL derived from the mapping specification, so the catalogue schema is a
controlled artefact traceable to `STTM-001` instead of a scanner's guess.

---

## Repository layout

```
docs/
  01-assessment/     Migration feasibility and gap assessment
  02-specifications/ User and functional requirements (URS / FS)
  03-mapping/        STTM-001 — source-to-target mapping  ← single source of truth
  04-traceability/   RTM, generated from the tests rather than written by hand
  05-data-integrity/ ALCOA+ and audit trail
  06-risk/           Risk assessment justifying test depth

infra/
  iam/               Least-privilege deployer policy
  provision/         CS-001 config spec, provisioner, and two qualification checks

src/
  legacy_sim/        Deterministic legacy extract generator + defect catalogue
  migration/         Extract → transform → load
  reconciliation/    Completeness, accuracy, integrity controls
  evidence/          Evidence assembly and RTM generation

tests/               Tagged with requirement IDs; the RTM is derived from these
data/legacy/         The exact dataset the evidence was produced from
data/_oracle/        Ground truth for known-answer tests (migration code must not read this)
evidence/            Execution records — these are the deliverable, not a by-product
```

`STTM-001` is consumed by the transform, by the reconciliation controls, and by
the RTM generator. A mapping specification maintained separately from the code it
describes drifts from it; here the specification *is* the implementation input,
so the two cannot silently disagree.

---

## Reproducing it

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt

# 1. Generate the legacy extract (deterministic from the seed)
cd src && python -m legacy_sim.generate_extract --out ../data/legacy --oracle ../data/_oracle

# 2. Verify the deployer credential scope
python infra/provision/verify_access.py --profile gxp-val

# 3. Provision the environment from CS-001
python infra/provision/provision.py --profile gxp-val --alert-email you@example.com

# 4. Installation qualification: as-built versus as-designed
python infra/provision/verify_install.py --profile gxp-val
```

Steps 2 and 4 write evidence records to `evidence/`.

---

## Two findings from building it

Both are recorded because they are the same class of problem this pack exists to
catch — a control that appears to work and does not.

**An IAM `Deny` that never fires.** The first deployer policy restricted Lambda
memory via a `lambda:MemorySize` condition key. That condition key does not
exist; AWS does not expose memory in the authorisation context. The statement
would have sat there looking like a guard rail while matching nothing. Two more
`Deny` entries used invalid service prefixes (`emr:*` instead of
`elasticmapreduce:*`, `opensearch:*` instead of `es:*`) with the same effect.

**A negative test that could not fail.** The first credential-scope check tested
denials by making real API calls against non-existent resources. S3 answered
`NoSuchBucket`, which at the client is indistinguishable from a denial — the
probe reported PASS on an inconclusive result. It was rewritten to use the IAM
policy simulator, which returns the authorisation decision itself and separates
an **explicit** deny (a guard rail fired) from an **implicit** one (the
permission was simply never granted).

---

## Cost

Designed to cost approximately nothing, and the design is enforced rather than
intended:

- **Free tier, always:** Lambda, Glue Data Catalog, CloudWatch Logs, SNS, EventBridge
- **Not free, so excluded:** Glue crawlers and ETL jobs, Redshift, KMS customer-managed keys, Kinesis, EMR, SageMaker
- **The only non-zero line:** Athena, at ~$0.00005 per query (10 MB minimum billing). Hundreds of queries cost a few cents.

Three independent controls: an IAM policy that explicitly denies every billable
service in the list above (verified by `IQ-AUTH-B07` through `B13`), an Athena
workgroup enforcing a 100 MB scan ceiling per query, and a $1 monthly budget that
functions as a tripwire rather than an allowance.

---

## Status

| Artefact | State |
|---|---|
| Legacy extract, 9 defects injected and byte-verified | ✅ 15.8k rows |
| `STTM-001` source-to-target mapping | ✅ 43 fields, 14 critical |
| `CS-001` configuration specification | ✅ |
| IAM deployer policy | ✅ 12 statements |
| `IQ-AUTH` credential scope qualification | ✅ 23/23 |
| `IQ-CONFIG` as-built versus as-designed | 🟡 32/33 — no confirmed SNS subscriber yet |
| Migration transform | 🔨 in progress |
| Reconciliation controls | 🔨 in progress |
| URS / FS, RTM, risk assessment, ALCOA+ note | 📋 pending |
