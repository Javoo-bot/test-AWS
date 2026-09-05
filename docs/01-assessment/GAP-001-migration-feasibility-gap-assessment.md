# GAP-001 · Migration Feasibility and Gap Assessment

| | |
|---|---|
| Document ID | GAP-001 |
| Version | 1.0 |
| Status | DRAFT |
| Scenario assessed | Migration of five years of historical donor screening records from LEGACY-LIS 4.2 to the AWS enterprise data platform |
| Assessed against | URS-001 v1.0 |
| Precedes | STTM-001, RA-001, RTM-001 |

---

## 1. Purpose

This assessment answers one question before any migration work is authorised:

> **Can the existing systems support the proposed migration scenario, and what is
> missing if they cannot?**

It is deliberately the first document in the pack. A source-to-target mapping written
before this assessment would be a mapping of assumptions.

## 2. Method

Each requirement in URS-001 was tested against what the source system can actually
provide, established by inspecting the extract itself rather than by reading the
legacy data dictionary. The dictionary was found to be incomplete in two respects
(GAP-04, GAP-07), which is why it was not treated as authoritative.

Gaps are rated by whether they **block** the migration, **constrain** it, or require
a **decision** from a named owner before proceeding.

---

## 3. Current state — what the source can provide

| Capability | Available | Notes |
|---|:--:|---|
| Full extract of the 2020–2024 window | ✅ | Pipe-delimited flat files, complete for the window |
| Stable primary keys | ⚠️ | Unique in intent; duplicates present in practice (GAP-01) |
| Referential integrity between entities | ⚠️ | Not enforced at extract time (GAP-02) |
| Declared control totals | ✅ | Manifest present, but not self-consistent (GAP-03) |
| Coded values documented | ⚠️ | Dictionary incomplete (GAP-04) |
| Units of measure recorded | ✅ | Recorded, but differ from target convention (GAP-05) |
| Timestamps with UTC offset | ❌ | **Not available at all** (GAP-06) |
| Character set declared | ⚠️ | Declared ISO-8859-1; not uniformly true (GAP-07) |
| Change history / audit trail in source | ❌ | **Not available** (GAP-08) |

## 4. Technical dependency map

What this migration depends on, and what breaks if each changes:

| Dependency | Type | If it changes |
|---|---|---|
| `SITE_CODE` → timezone lookup | **Derived, not sourced** | Every timestamp in the migration becomes unresolvable. This lookup is an input to the migration, not an output of it, and must be version-controlled with the mapping. |
| Legacy assay code → LOINC map | External vocabulary | New legacy codes appearing after cut-off migrate unmapped |
| Result code vocabulary | Closed set | An unrecognised code halts the affected row by design |
| Extract atomicity across tables | Process | Non-atomic extracts create orphans (observed, GAP-02) |
| Target unit convention | Target-side | A unit change after mapping invalidates conversion factors |

The first row is the significant one. The migration cannot resolve a single timestamp
using only source data; it depends on an external mapping that the legacy system does
not contain. That dependency is invisible in the source schema, and would be easy to
discover late.

---

## 5. Gap register

| ID | Gap | Class | Owner | Disposition |
|---|---|---|---|---|
| GAP-01 | Donation identifiers are duplicated across a historical site-database merge | **Decision** | Data Owner (Laboratory) | Quarantine all rows sharing the key. No survivor is selected: without provenance there is no basis for preferring one row, and the identifier is printed on a physical blood unit. |
| GAP-02 | Result and donation extracts were not taken atomically, producing orphans | **Decision** | Data Owner (Laboratory) | Quarantine orphans. Recommend a re-extract with a single consistent cut-off before production migration. |
| GAP-03 | Manifest control totals disagree with the files they describe | **Blocking** | Source System Owner | Must be resolved before production migration. Source-side totals cannot be trusted, so target reconciliation would be meaningless. Migration may proceed in validation only. |
| GAP-04 | Legacy data dictionary omits the sentinel `9999` meaning "test not performed" | **Decision** | Data Steward | Mapped to `NOT_PERFORMED`, which is the absence of a result rather than a result. Requires confirmation by the laboratory. |
| GAP-05 | Haemoglobin recorded in `g/dL`; target convention is `g/L` | **Constraint** | Data Steward | Conversion applied and factor recorded per record. |
| GAP-06 | Source stores local wall-clock time with **no UTC offset anywhere in the schema** | **Constraint** | Migration Lead | Offset derived from site. Instants inside the DST fallback hour are irreducibly ambiguous and are flagged, not resolved. |
| GAP-07 | A subset of donor names was double-encoded at rest before this project | **Decision** | Data Owner (Laboratory) | Repair where unambiguous; carry original forward and flag where speculative. Requires a decision on whether repair is acceptable at all. |
| GAP-08 | Source holds no audit trail, so pre-migration history cannot be reconstructed | **Accepted limitation** | Quality Unit | Cannot be closed by this project. The target audit trail begins at migration; the absence of prior history is a known and documented limitation, not a defect introduced here. |
| GAP-09 | Two legacy assays have no agreed target vocabulary code | **Decision** | Laboratory SME | Rows loaded with the legacy code retained and the gap reported. Not dropped. |

### Class definitions

- **Blocking** — production migration must not proceed until resolved.
- **Constraint** — proceeds, but the mapping must compensate and record how.
- **Decision** — proceeds only once a named owner records a documented choice.
- **Accepted limitation** — cannot be closed; documented so it is not mistaken for an oversight.

---

## 6. Feasibility verdict

**The migration is feasible, with one blocking gap and six requiring documented
disposition.**

| | |
|---|---|
| Requirements in URS-001 that the source can satisfy directly | 7 of 13 |
| Requirements satisfiable only with a derived input | 2 (URS-004, URS-005) |
| Requirements satisfiable only with a documented decision | 3 (URS-003, URS-007, URS-011) |
| Requirements the source cannot satisfy at all | 1 (pre-migration history, GAP-08) |

**GAP-03 blocks production.** A manifest that disagrees with its own files means the
source cannot state how many records it holds. Reconciling the target against that
figure would produce agreement on a number nobody has verified — the appearance of a
control rather than a control.

The remaining gaps are all workable, and every one of them was **found by inspecting
the data rather than by reading the documentation about it**. That is the practical
conclusion of this assessment: for a system of this age, the extract is the
authoritative description of the source, and the dictionary is a hypothesis.

## 7. Recommendations

1. **Re-extract with a single atomic cut-off** across all four entities before
   production migration. This closes GAP-02 at source rather than compensating for it.
2. **Resolve GAP-03 with the source system owner.** Either the manifest generation or
   the extract sequence is wrong; the migration cannot determine which.
3. **Obtain written disposition** for GAP-01, GAP-04, GAP-07 and GAP-09 before
   production. Each is a data decision, not a technical one.
4. **Version-control the site-to-timezone lookup with the mapping specification.** It
   is a migration input that exists nowhere in the source system, and losing it makes
   every migrated timestamp unverifiable after the fact.
5. **Record GAP-08 in the target system documentation**, so that the absence of
   pre-migration history is understood as a property of the source rather than a
   deficiency in the target.

---

<sub>GAP-001 v1.0 · assessed against URS-001 v1.0 · gaps carried forward into RA-001 and STTM-001</sub>
