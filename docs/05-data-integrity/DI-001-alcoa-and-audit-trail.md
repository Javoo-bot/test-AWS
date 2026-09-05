# DI-001 · ALCOA+ and Audit Trail

| | |
|---|---|
| Document ID | DI-001 |
| Version | 1.0 |
| Status | DRAFT |
| Scope | How data integrity principles are applied to the migration itself |
| Evidence | `data/target/audit.jsonl` · `data/target/quarantine.jsonl` · `evidence/` |

---

## 1. The distinction this document rests on

ALCOA+ is usually applied to how a system records data during operation. A migration
is different: it is a bulk, one-time rewriting of records that already exist.

That makes one principle dominant. **Original** is the hard one. Every other principle
can be satisfied by a system that quietly improves data as it moves. Original is the
one that forbids it.

So the design rule throughout is narrower than "be accurate":

> A transformed value never replaces its source. It accompanies it.

---

## 2. Principle by principle

### Attributable

Every record carries who and what produced it, and every transformation carries which
rule changed it.

| Implementation | Where |
|---|---|
| `operator_id` and `instrument_id` retained on every result and donation | `screening_result`, `donation` |
| Every audit entry names the rule and the requirement it serves | `audit.jsonl` → `rule`, `requirement` |
| Migration run identity and execution window | `run_summary.json` → `run_id`, `started_utc` |
| Infrastructure changes attributed to a principal | `evidence/IQ-INSTALL-environment.json` → `principal_arn` |

The audit entry attributes the *decision*, not merely the access. Knowing that a row
was read is weaker evidence than knowing which rule rewrote it and why.

### Legible

Records are readable without the originating system. Output is UTF-8 JSON with named
fields; the source was Latin-1 pipe-delimited flat files whose meaning depended on a
data dictionary that turned out to be incomplete (GAP-04, GAP-07).

Legibility is also why `.gitattributes` marks the extract files `-text`: line-ending
normalisation in transit would silently invalidate the SHA-256 checksums that prove
the files are the ones the evidence was produced from.

### Contemporaneous

| Implementation | Where |
|---|---|
| Every audit entry timestamped at the moment of the decision | `audit.jsonl` |
| Every quarantine record timestamped at rejection | `quarantine.jsonl` → `quarantined_utc` |
| Evidence records timestamped at execution, not at authoring | `evidence/*.json` → `executed_utc` |

Contemporaneous applies to the *migration's own* records. The source records' original
timing is preserved separately — see Original.

### Original

The principle this migration is built around.

| What could have been lost | What is retained instead |
|---|---|
| Local wall-clock time, replaced by UTC | **Both.** `collection_ts_local` holds the untouched source string alongside `collection_ts_utc`, plus `source_timezone` for the offset the source never recorded. |
| Free-text result strings, replaced by parsed numbers | **Both.** `result_value_raw` holds the source string verbatim next to `result_value_numeric`. |
| Censoring destroyed by parsing `<0.10` as `0.10` | **Preserved.** The numeric column stays null and `result_value_operator` carries the comparator. "Below the limit of detection" and "measured 0.10" are different assertions. |
| Legacy assay code discarded once mapped | **Both.** `assay_code_source` is always retained, mapped or not. |
| Unit conversion buried in code | **Exposed.** `unit_conversion_factor` records the arithmetic per record. |
| Corrupt names silently repaired | **Recorded.** Every repair carries the original and its source bytes in hex. Speculative repairs are not applied at all. |
| Rejected rows discarded | **Retained.** Every quarantined record holds its complete source row verbatim. |

S3 object versioning supports this at the storage layer: writes are additive, so an
overwrite never destroys the prior state.

### Accurate

Accuracy here means *meaning* preserved, not merely values copied.

- Synonyms collapse (`NR`, `N`, `NEG` → `NON_REACTIVE`) but distinct clinical states
  do not. `INITIAL_REACTIVE` and `REPEAT_REACTIVE` are different determinations, and a
  test asserts they remain separate.
- Unrecognised codes are **rejected, never defaulted**. Guessing at a screening result
  is the one failure mode with a direct patient-safety consequence.
- `qc_status` is carried verbatim and never recomputed — see RA-001 §6.

### Complete

`migrated + quarantined = source`, exactly, verified per entity. For the current run:
**15,839 = 15,733 + 106**, with zero unaccounted.

No record is silently dropped. Quarantine is a queue for human disposition, not a bin,
and every entry names the rule it violated and the disposition owner.

### Consistent

One mapping specification (`STTM-001`) is the input to the transform, the
reconciliation controls **and** the traceability matrix. A specification maintained
separately from the code it describes drifts from it; here it is the implementation
input, so the two cannot disagree without failing.

### Enduring

Records are held in open, non-proprietary formats — JSON and Parquet — readable
without the migration tooling. S3 versioning is enabled with a defined lifecycle. The
extract files carry SHA-256 checksums reconciled by control REC-C01, so future readers
can prove they hold the same bytes.

### Available

Evidence is version-controlled and retrievable without running anything. The
traceability matrix regenerates from its inputs, so it can be reproduced rather than
merely trusted.

---

## 3. What the audit trail deliberately does *not* record

Decisions with status `ok` — where a value passed through unchanged — are not written.

This is a design choice with a reason. An audit trail containing every untouched field
would run to hundreds of thousands of entries, and noise is where real entries hide. A
trail that cannot be read is not a control.

What must be reconstructible is **every point at which the migration changed or
questioned the source**. Those are recorded exhaustively: 24,773 entries across
22,030 repairs, 2,743 flags and every rejection. A test asserts that no `ok` decision
appears in the trail, so the rule holds by verification rather than by intention.

## 4. Known limitation

The source system holds **no audit trail of its own** (GAP-08). Pre-migration history
cannot be reconstructed, because it was never kept.

The target audit trail therefore begins at migration. This is recorded here so that
the absence is understood as a property of the source system rather than a deficiency
introduced by this project, and so that nobody later mistakes it for an area that was
simply not examined.

---

<sub>DI-001 v1.0 · evidence in `data/target/audit.jsonl` and `evidence/` · limitations traced to GAP-001</sub>
