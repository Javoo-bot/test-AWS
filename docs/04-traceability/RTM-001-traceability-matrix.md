# RTM-001 · Requirements Traceability Matrix

> **Generated, not written.** Produced by `src/evidence/build_rtm.py` from URS-001, STTM-001 and the evidence records. Do not edit by hand: regenerate it.

| | |
|---|---|
| Generated | 2026-09-05 11:22 UTC |
| Requirements | 13 |
| Controls | 12 |
| Migration run | `MIG-20260905T111906Z` |
| Known-answer run | `2026-09-05T11:19:07` |


## Coverage

| Status | Count | Meaning |
|---|---|---|
| **VERIFIED** | 13 | control implemented, exercised by a defect, all contained |
| **PARTIAL** | 0 | exercised, but something was not fully contained |
| **UNEXERCISED** | 0 | control implemented but nothing tests it |
| **GAP** | 0 | no control, or control not implemented |

## Matrix

| Req | Crit | Requirement | Controls | Fields | Defects | Tests | Status |
|---|---|---|---|---|---|---|---|
| `URS-001` | critical | All records within the agreed extract window shall be migrated, and every source record sh… | `REC-C01` `REC-C02` | 3 | `DEF-09` | 1 | **VERIFIED** |
| `URS-002` | critical | Each donation shall remain traceable to the donor who gave it. | `REC-I02` | 4 | `DEF-07` | 1 | **VERIFIED** |
| `URS-003` | critical | The screening result determining unit disposition shall be migrated without alteration of … | `REC-A04` `REC-C03` | 5 | `DEF-01` `DEF-08` | 1 | **VERIFIED** |
| `URS-004` | high | All timestamps shall be expressed unambiguously in UTC, with the source local value and th… | `REC-I04` | 6 | `DEF-05` | 2 | **VERIFIED** |
| `URS-005` | critical | Legacy coded values shall map to the target controlled vocabulary. Unmapped codes shall be… | `REC-A04` `REC-C03` | 8 | `DEF-01` `DEF-08` | — | **VERIFIED** |
| `URS-006` | critical | Numeric results shall be migrated with their unit, converted where source and target units… | `REC-A01` `REC-A02` | 9 | `DEF-02` `DEF-03` | 2 | **VERIFIED** |
| `URS-007` | high | Character fidelity of donor names shall be preserved. Repairs of pre-existing corruption s… | `REC-A03` | 3 | `DEF-04` | — | **VERIFIED** |
| `URS-008` | critical | Primary key uniqueness shall be enforced in the target. | `REC-I01` | 3 | `DEF-06` | 1 | **VERIFIED** |
| `URS-009` | critical | Referential integrity between results and donations shall be enforced. | `REC-I02` | 2 | `DEF-07` | 1 | **VERIFIED** |
| `URS-010` | critical | Every transformation that alters, flags or rejects a value shall be recorded in an audit t… | `REC-A05` | 10 | — | 4 | **VERIFIED** |
| `URS-011` | critical | No record shall be silently discarded. Records that cannot be migrated in conformance with… | `REC-C02` `REC-I03` | 3 | — | 2 | **VERIFIED** |
| `URS-012` | high | Source control totals shall be reconciled against the source files themselves before any c… | `REC-C01` | — | `DEF-09` | — | **VERIFIED** |
| `URS-013` | high | Direct personal identifiers not required downstream shall be pseudonymised on load. | `REC-A05` | 1 | — | 2 | **VERIFIED** |

## Controls

| Control | Level | Verifies | Implemented in |
|---|---|---|---|
| `REC-C01` | portfolio | `URS-001` `URS-012` | `src/migration/pipeline.py::control_c01_manifest` |
| `REC-C02` | portfolio | `URS-001` `URS-011` | `src/migration/pipeline.py::main` |
| `REC-C03` | field | `URS-003` `URS-005` | `src/migration/transforms.py::map_assay` |
| `REC-A01` | field | `URS-006` | `src/migration/transforms.py::normalise_numeric` |
| `REC-A02` | field | `URS-006` | `src/migration/transforms.py::convert_unit` |
| `REC-A03` | field | `URS-007` | `src/migration/transforms.py::repair_double_encoding` |
| `REC-A04` | field | `URS-003` `URS-005` | `src/migration/transforms.py::map_result_code` |
| `REC-A05` | record | `URS-010` `URS-013` | `src/migration/pipeline.py::Run.audit_decision` |
| `REC-I01` | data object | `URS-008` | `src/migration/pipeline.py::control_i01_uniqueness` |
| `REC-I02` | data object | `URS-002` `URS-009` | `src/migration/pipeline.py::control_i02_referential` |
| `REC-I03` | record | `URS-011` | `src/migration/pipeline.py::Run.reject` |
| `REC-I04` | field | `URS-004` | `src/migration/transforms.py::to_utc` |

---
<sub>RTM-001 · generated 2026-09-05 11:22 UTC · regenerate with `python -m evidence.build_rtm`</sub>
