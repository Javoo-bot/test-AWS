# RA-001 · Risk Assessment and Risk-Based Testing Justification

| | |
|---|---|
| Document ID | RA-001 |
| Version | 1.0 |
| Status | DRAFT |
| Method | GAMP 5 two-stage: system-level GxP impact, then functional risk assessment |
| Inputs | URS-001, GAP-001, STTM-001 |
| Output | Test depth per data element, and the justification for testing some things less |

---

## 1. Why this document exists

Testing everything to the same depth is not rigour, it is an absence of judgement.
It also fails in practice: effort spread evenly is effort taken away from the fields
that can hurt someone.

This document decides **where the effort goes**, and — more importantly — records
**what is deliberately tested less, and why**. An auditor can disagree with a
justification. They cannot disagree with one that was never written down.

## 2. System-level GxP impact

| Question | Determination |
|---|---|
| Does the system hold GxP-regulated records? | **Yes.** Donor screening results are records supporting the release of a medicinal product for human use. |
| GAMP software category | **Category 5** (bespoke). The migration logic is written for this purpose and has no established use history. |
| Patient safety impact | **Direct.** A screening result determines whether a blood unit is released for transfusion or destroyed. |
| Product quality impact | **Direct.** The unit is the product. |
| Data integrity impact | **Direct.** Records must remain attributable and traceable for the retention period. |

**Conclusion:** the migration is a GxP-impacting, Category 5 activity. Functional risk
assessment is therefore required at field level, not at system level alone.

---

## 3. Scoring method

Applied in the two stages GAMP 5 specifies.

**Stage 1 — Severity × Probability → Risk Class**

| Severity ↓ / Probability → | Low | Medium | High |
|---|:--:|:--:|:--:|
| **High** — patient harm possible | 2 | 1 | 1 |
| **Medium** — record integrity or traceability lost | 3 | 2 | 1 |
| **Low** — operational inconvenience | 3 | 3 | 2 |

**Stage 2 — Risk Class × Detectability → Risk Priority**

| Risk Class ↓ / Detectability → | High (caught easily) | Medium | Low (silent) |
|---|:--:|:--:|:--:|
| **1** | Medium | High | **High** |
| **2** | Low | Medium | **High** |
| **3** | Low | Low | Medium |

Detectability is doing real work in this assessment. Several of the defects in this
dataset are **low-detectability by nature** — they produce output that is
well-formed, plausible and wrong. Those escalate regardless of how unlikely they are.

---

## 4. Functional risk assessment

| Data element | S | P | D | Class | **Priority** | Driving concern |
|---|:--:|:--:|:--:|:--:|:--:|---|
| `result_code` (screening outcome) | High | Med | Low | 1 | **HIGH** | Determines unit release or destruction. A merged synonym is invisible downstream. |
| `donation_id` | High | Med | Med | 1 | **HIGH** | Printed on the physical unit. A collapsed duplicate makes the database disagree with the bag. |
| `donor_id` link | High | Low | Med | 2 | **HIGH** | Breaks lookback. If a donor is later found infected, unrecalled units stay in circulation. |
| `result_value_numeric` + unit | High | Med | **Low** | 1 | **HIGH** | A tenfold unit error is well-formed and passes every schema check. |
| `assay_code` mapping | High | High | Med | 1 | **HIGH** | An unmapped assay silently detaches a result from what it measured. |
| `abo_group`, `rh_type` | High | Low | High | 2 | Medium | Severe if wrong, but a closed vocabulary makes violations obvious. |
| `qc_status` | Med | Low | Med | 3 | Medium | QC is the evidence a run was in control; without it a result is undefendable. |
| Timestamps (UTC conversion) | Med | High | Low | 1 | **HIGH** | No offset in source. Errors are systematic, not random, and look correct. |
| Donor name characters | Med | Med | Med | 2 | Medium | Affects identification and matching, not clinical decision. |
| `operator_id`, `instrument_id` | Med | Low | High | 3 | Low | Attributable metadata; absence is immediately visible. |
| `volume_ml` | Low | Low | High | 3 | Low | Operational. Out-of-range values are obvious. |
| `component_type` | Low | Low | High | 3 | Low | Closed vocabulary, small cardinality. |
| `sex`, descriptive fields | Low | Low | High | 3 | Low | No clinical decision depends on them in this dataset. |

---

## 5. Test depth, derived from priority

| Priority | Test depth applied | Rationale |
|---|---|---|
| **HIGH** | 100% field-level verification. No sampling. Known-answer defect injection. Dedicated reconciliation control. | A sampled check on a field that decides unit disposition leaves a known proportion unverified, and no defensible proportion exists. |
| **Medium** | 100% structural verification (vocabulary, nullability, uniqueness) plus aggregate comparison. No per-row value assertion. | Violations are detectable in aggregate because the value domain is closed and small. |
| **Low** | Row-count and completeness verification only. | Failures are visible on inspection and carry no patient-safety consequence. |

## 6. What is deliberately tested less — and why

This is the section the assessment exists for.

**Descriptive donor fields (`sex`, `site_code`, `component_type`)** are verified for
presence and vocabulary conformance only, with no per-row value comparison. They do
not participate in any clinical decision in this dataset, and their value domains are
small enough that a systematic error would appear in the aggregate.

**`volume_ml`** is range-checked, not reconciled per record. A wrong volume is an
operational data-quality issue, not a safety one, and it is visible to anyone who
looks at the distribution.

**`qc_status` is carried through verbatim and deliberately NOT recomputed** from the
expected and observed values. This is a decision, not an omission. The recorded status
is what the laboratory concluded at the time, under the procedures then in force.
Recalculating it would replace a historical record with a present-day opinion, which
is precisely what a migration must not do. Where a recomputation would disagree with
the record, that is reported as a finding for the data owner rather than silently
corrected.

**Pre-migration audit history is not verified at all**, because it does not exist
(GAP-08). No amount of testing can recover history the source never kept. This is
recorded as an accepted limitation so it is not later mistaken for an untested area.

**Performance and volume testing are out of scope.** This is a one-time historical
migration of a bounded dataset, not an operational interface. Throughput has no
patient-safety dimension here, and testing it would consume effort that belongs on
the HIGH-priority fields.

## 7. Residual risk

| Residual risk | Why it cannot be eliminated | Control |
|---|---|---|
| Timestamps inside the DST fallback hour are irreducibly ambiguous | The source did not record the offset. The information is gone, not hidden. | Resolved deterministically to the first occurrence, **flagged** in the target, and the alternative value recorded in the audit trail. A consumer can see the uncertainty exists. |
| Double-encoding repair may be imperfect where a name legitimately contains the marker characters | Repair is inference; certainty is not available | Repair applied only where a mojibake marker is present. Ambiguous cases carry the original forward and are flagged, never silently altered. |
| Two assays remain unmapped | No agreed target code exists yet | Rows loaded with the legacy code retained, gap reported to the SME. Not dropped. |

Each residual risk is **visible in the target data**, not merely recorded here. A
residual risk documented in a report but invisible in the database is a risk that has
been described rather than managed.

---

<sub>RA-001 v1.0 · GAMP 5 two-stage functional risk assessment · test depth traced in RTM-001</sub>
