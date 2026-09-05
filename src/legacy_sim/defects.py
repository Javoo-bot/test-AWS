"""
Catalogue of data defects deliberately injected into the simulated legacy extract.

Rationale: a migration validation pack in which every check passes demonstrates
nothing. Each defect below is drawn from a failure mode that occurs routinely in
real LIS decommissioning projects. Every one is traceable to a requirement, a
mapping rule, a test and a risk rating.

The ground-truth injection log is written to a directory that the migration and
reconciliation code must never read; only the test oracle consumes it. This makes
the suite a known-answer test rather than a self-fulfilling one.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Defect:
    id: str
    name: str
    description: str
    dimension: str        # completeness | accuracy | integrity | consistency
    detected_by: str      # which reconciliation control is expected to catch it


DEFECT_CATALOGUE = [
    Defect(
        id="DEF-01",
        name="Unmapped legacy assay code",
        description=(
            "Historical rows reference proprietary assay codes (LOC900 Hb pre-donation, "
            "LOC901 HTLV I/II legacy) that have no agreed code in the target vocabulary. "
            "The assay was retired before the target system was specified."
        ),
        dimension="completeness",
        detected_by="REC-C03 assay code coverage",
    ),
    Defect(
        id="DEF-02",
        name="Unit of measure mismatch",
        description=(
            "Haemoglobin is recorded in g/dL in the legacy LIS and g/L in the target. "
            "A migration that copies the number without conversion understates the value "
            "by a factor of ten."
        ),
        dimension="accuracy",
        detected_by="REC-A02 unit-aware value comparison",
    ),
    Defect(
        id="DEF-03",
        name="Censored and whitespace-padded numeric results",
        description=(
            "Signal-to-cutoff ratios are stored as free text. Values below the assay's "
            "limit of detection appear as '<0.10'; others carry leading/trailing spaces "
            "or use a comma decimal separator. Naive casting to numeric yields nulls."
        ),
        dimension="accuracy",
        detected_by="REC-A01 numeric parse and null-delta check",
    ),
    Defect(
        id="DEF-04",
        name="Pre-existing character encoding corruption",
        description=(
            "A subset of donor names was double-encoded at rest in the legacy system "
            "(UTF-8 bytes stored as Latin-1), so 'Nuria' with an accent appears as mojibake. "
            "The corruption predates the migration; the pack must decide whether to repair "
            "or carry it forward, and record that disposition."
        ),
        dimension="accuracy",
        detected_by="REC-A03 character set validation",
    ),
    Defect(
        id="DEF-05",
        name="Timezone-ambiguous timestamps",
        description=(
            "The legacy LIS stores local wall-clock time with no UTC offset. Sites span "
            "two offsets (peninsular Spain and the Canary Islands), and timestamps falling "
            "in the October DST fallback hour are genuinely ambiguous."
        ),
        dimension="consistency",
        detected_by="REC-I04 temporal integrity check",
    ),
    Defect(
        id="DEF-06",
        name="Duplicate donation identifier",
        description=(
            "The same donation identifier appears on more than one row with differing "
            "content, an artefact of a historical merge between two site databases. The "
            "target enforces uniqueness, so one row would be silently lost on load."
        ),
        dimension="integrity",
        detected_by="REC-I01 primary key uniqueness",
    ),
    Defect(
        id="DEF-07",
        name="Orphan result rows",
        description=(
            "Result rows reference donation identifiers absent from the donation extract, "
            "because the extract windows for the two tables were not taken atomically."
        ),
        dimension="integrity",
        detected_by="REC-I02 referential integrity",
    ),
    Defect(
        id="DEF-08",
        name="Undocumented sentinel value",
        description=(
            "The result code '9999' is used to mean 'test not performed'. It is absent "
            "from the legacy data dictionary and would migrate as a literal result code."
        ),
        dimension="accuracy",
        detected_by="REC-C03 assay code coverage / REC-A04 vocabulary conformance",
    ),
    Defect(
        id="DEF-09",
        name="Source manifest control total mismatch",
        description=(
            "The extract manifest declares a row count taken before a late-arriving row "
            "was written. Source-side control totals must be reconciled against the files "
            "themselves before any comparison with the target is meaningful."
        ),
        dimension="completeness",
        detected_by="REC-C01 source manifest reconciliation",
    ),
]

BY_ID = {d.id: d for d in DEFECT_CATALOGUE}
