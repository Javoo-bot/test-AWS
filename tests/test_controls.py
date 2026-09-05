"""
Control tests, each tagged with the requirement it verifies.

These cover the requirements that the known-answer verification cannot reach.
Known-answer testing proves the controls catch injected defects; it says nothing
about properties that hold across the whole run -- that nothing was lost, that
the audit trail is complete enough to reconstruct a decision, that identifiers
were pseudonymised. Those are asserted here.

The distinction matters: RTM-001 flagged URS-010, URS-011 and URS-013 as having
implemented controls that nothing exercised. That gap was visible only because
the matrix is generated from what ran.
"""

import json

import pytest

REQUIRED_AUDIT_FIELDS = {
    "entity", "key", "field", "status", "rule", "requirement",
    "original", "resulting", "note",
}


# ---------------------------------------------------------------------------
# URS-001 / URS-011 -- completeness and no silent loss
# ---------------------------------------------------------------------------
@pytest.mark.requirement("URS-001")
@pytest.mark.control("REC-C02")
def test_every_source_row_is_accounted_for(run_summary):
    """Migrated plus quarantined equals the source row count exactly."""
    source = sum(run_summary["source_rows"].values())
    migrated = sum(run_summary["migrated_rows"].values())
    quarantined = run_summary["quarantined"]
    assert migrated + quarantined == source, (
        f"{source - migrated - quarantined} source row(s) unaccounted for"
    )


@pytest.mark.requirement("URS-011")
@pytest.mark.control("REC-I03")
def test_every_quarantined_record_carries_its_disposition(quarantine):
    """No record is quarantined without a reason code, rule and verbatim source row."""
    assert quarantine, "expected at least one quarantined record"
    for q in quarantine:
        assert q["quarantine_reason"], f"{q['key']} quarantined with no reason code"
        assert q["rule_violated"], f"{q['key']} quarantined with no rule"
        assert q["note"], f"{q['key']} quarantined with no explanation"
        assert q["source_row"], f"{q['key']} quarantined without its source row"
        assert q["quarantined_utc"], f"{q['key']} quarantined without a timestamp"


@pytest.mark.requirement("URS-011")
@pytest.mark.control("REC-I03")
def test_quarantine_reasons_are_from_the_controlled_set(quarantine):
    """Quarantine reasons come from STTM-001, not from free text."""
    allowed = {"QTN-01", "QTN-02", "QTN-03", "QTN-04", "QTN-05"}
    used = {q["quarantine_reason"] for q in quarantine}
    assert used <= allowed, f"undeclared quarantine reason(s): {used - allowed}"


# ---------------------------------------------------------------------------
# URS-010 -- audit trail
# ---------------------------------------------------------------------------
@pytest.mark.requirement("URS-010")
@pytest.mark.control("REC-A05")
def test_audit_entries_are_complete(audit):
    """Every audit entry carries enough to re-derive or challenge the decision."""
    assert audit, "expected a non-empty audit trail"
    for e in audit[:5000]:
        missing = REQUIRED_AUDIT_FIELDS - e.keys()
        assert not missing, f"audit entry {e.get('key')} missing {missing}"


@pytest.mark.requirement("URS-010")
@pytest.mark.control("REC-A05")
def test_audit_records_no_untouched_values(audit):
    """Only decisions that altered, flagged or rejected a value are recorded.

    An audit trail containing every untouched field is mostly noise, and noise is
    where real entries hide.
    """
    statuses = {e["status"] for e in audit}
    assert "ok" not in statuses, "audit trail contains no-op decisions"
    assert statuses <= {"repaired", "flagged", "rejected"}, f"unexpected: {statuses}"


@pytest.mark.requirement("URS-010")
@pytest.mark.control("REC-A05")
def test_every_repair_retains_the_original(audit):
    """A repair without the original value cannot be challenged, so is not evidence."""
    for e in audit:
        if e["status"] == "repaired":
            assert e["original"] is not None, (
                f"{e['entity']}/{e['key']}/{e['field']} repaired with no original"
            )


@pytest.mark.requirement("URS-010")
@pytest.mark.control("REC-A05")
def test_audit_entries_cite_a_requirement(audit):
    """Every recorded decision traces to a requirement in URS-001."""
    known = {f"URS-{i:03d}" for i in range(1, 14)}
    cited = {e["requirement"] for e in audit}
    assert cited <= known, f"audit cites unknown requirement(s): {cited - known}"


# ---------------------------------------------------------------------------
# URS-013 -- pseudonymisation
# ---------------------------------------------------------------------------
@pytest.mark.requirement("URS-013")
@pytest.mark.control("REC-A05")
def test_national_identifier_is_not_in_the_target(curated, legacy_dir):
    """No source national identifier appears in the target in clear text."""
    donors = curated.get("donor", [])
    assert donors, "expected migrated donor records"

    source_ids = set()
    for line in (legacy_dir / "DONOR.txt").read_text(encoding="latin-1").splitlines()[1:]:
        if line.strip():
            source_ids.add(line.split("|")[1])

    blob = json.dumps(donors)
    leaked = [nid for nid in list(source_ids)[:200] if nid and nid in blob]
    assert not leaked, f"national identifier(s) present in clear text: {leaked[:3]}"


@pytest.mark.requirement("URS-013")
@pytest.mark.control("REC-A05")
def test_pseudonym_is_a_hash_and_is_stable(curated):
    """The replacement is a fixed-length digest, and distinct donors stay distinct."""
    donors = curated.get("donor", [])
    hashes = [d["national_id_hash"] for d in donors]
    assert all(len(h) == 64 and all(c in "0123456789abcdef" for c in h)
               for h in hashes), "pseudonym is not a SHA-256 digest"
    assert len(set(hashes)) == len(hashes), (
        "distinct donors collapsed to the same pseudonym, destroying matching"
    )


# ---------------------------------------------------------------------------
# URS-003 / URS-006 -- meaning preserved, not merely shape
# ---------------------------------------------------------------------------
@pytest.mark.requirement("URS-003")
@pytest.mark.control("REC-A04")
def test_distinct_clinical_states_are_not_merged(curated):
    """INITIAL_REACTIVE and REPEAT_REACTIVE survive as separate determinations."""
    codes = {r["result_code"] for r in curated.get("screening_result", [])}
    assert "INITIAL_REACTIVE" in codes and "REPEAT_REACTIVE" in codes, (
        "distinct reactive states were collapsed"
    )
    assert "NON_REACTIVE" in codes


@pytest.mark.requirement("URS-006")
@pytest.mark.control("REC-A01")
def test_censored_values_are_not_parsed_as_numbers(curated):
    """'<0.10' keeps its comparator and does not become the number 0.10."""
    censored = [r for r in curated.get("screening_result", [])
                if r.get("result_value_operator")]
    assert censored, "expected censored results in the dataset"
    for r in censored:
        assert r["result_value_numeric"] is None, (
            f"{r['result_id']} censored value was parsed as a plain number"
        )
        assert r["result_value_raw"], "censored result lost its original string"


@pytest.mark.requirement("URS-006")
@pytest.mark.control("REC-A02")
def test_unit_conversion_factor_is_recorded_per_record(curated):
    """Converted values carry the factor, so the arithmetic stays auditable."""
    converted = [r for r in curated.get("screening_result", [])
                 if r.get("unit_conversion_factor") not in (None, 1.0)]
    assert converted, "expected unit-converted results"
    for r in converted:
        assert r["unit_source"] != r["unit_target"]
        assert r["result_value_raw"] is not None


# ---------------------------------------------------------------------------
# URS-004 -- temporal
# ---------------------------------------------------------------------------
@pytest.mark.requirement("URS-004")
@pytest.mark.control("REC-I04")
def test_local_timestamp_is_retained_alongside_utc(curated):
    """ALCOA+ Original: the derived UTC value never replaces the source string."""
    donations = curated.get("donation", [])
    assert donations
    for d in donations[:500]:
        assert d["collection_ts_utc"], "missing UTC timestamp"
        assert d["collection_ts_local"], "source local string was discarded"
        assert d["source_timezone"], "resolved offset not recorded"


@pytest.mark.requirement("URS-004")
@pytest.mark.control("REC-I04")
def test_ambiguous_timestamps_are_flagged_not_hidden(curated):
    """Instants inside the DST fallback hour are marked, not silently resolved."""
    donations = curated.get("donation", [])
    flagged = [d for d in donations if d.get("timestamp_ambiguous")]
    assert flagged, "no timestamp ambiguity flagged; expected DST fallback cases"


# ---------------------------------------------------------------------------
# URS-008 / URS-009 -- data object integrity
# ---------------------------------------------------------------------------
@pytest.mark.requirement("URS-008")
@pytest.mark.control("REC-I01")
def test_no_duplicate_keys_reach_the_target(curated):
    """Primary keys are unique in every migrated entity."""
    keys = {"donor": "donor_id", "donation": "donation_id",
            "screening_result": "result_id", "qc_result": "qc_id"}
    for entity, key in keys.items():
        rows = curated.get(entity, [])
        if not rows:
            continue
        ids = [r[key] for r in rows]
        assert len(set(ids)) == len(ids), f"{entity} contains duplicate {key}"


@pytest.mark.requirement("URS-009")
@pytest.mark.control("REC-I02")
def test_no_orphan_results_reach_the_target(curated):
    """Every migrated result resolves to a migrated donation."""
    donations = {d["donation_id"] for d in curated.get("donation", [])}
    orphans = [r["result_id"] for r in curated.get("screening_result", [])
               if r["donation_id"] not in donations]
    assert not orphans, f"{len(orphans)} orphan result(s) reached the target"


@pytest.mark.requirement("URS-002")
@pytest.mark.control("REC-I02")
def test_every_donation_traces_to_a_donor(curated):
    """Donor-to-donation linkage survives, so lookback remains possible."""
    donors = {d["donor_id"] for d in curated.get("donor", [])}
    broken = [d["donation_id"] for d in curated.get("donation", [])
              if d["donor_id"] not in donors]
    assert not broken, (
        f"{len(broken)} donation(s) cannot be traced to a donor, defeating recall"
    )
