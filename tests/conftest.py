"""
Pytest configuration that turns the test run into a traceable evidence record.

Each test declares the requirement it verifies with @pytest.mark.requirement.
The results are written to evidence/OQ-TEST-controls.json, which the traceability
matrix generator then reads. That is what lets RTM-001 be derived from tests that
actually executed rather than from a claim that they exist.
"""

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

EVIDENCE = ROOT / "evidence" / "OQ-TEST-controls.json"

_results = []


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "requirement(id): the URS requirement this test verifies")
    config.addinivalue_line(
        "markers", "control(id): the reconciliation control this test exercises")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when != "call":
        return

    reqs = [m.args[0] for m in item.iter_markers(name="requirement")]
    ctls = [m.args[0] for m in item.iter_markers(name="control")]
    if not reqs:
        return

    _results.append({
        "test": item.nodeid.split("::", 1)[-1],
        "requirements": reqs,
        "controls": ctls,
        "outcome": report.outcome,
        "duration_s": round(report.duration, 4),
        "docstring": (item.function.__doc__ or "").strip().split("\n")[0],
    })


def pytest_sessionfinish(session, exitstatus):
    if not _results:
        return
    passed = sum(1 for r in _results if r["outcome"] == "passed")
    record = {
        "protocol": "OQ-TEST",
        "title": "Requirement-tagged control tests",
        "executed_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "tests": _results,
        "summary": {
            "total": len(_results),
            "passed": passed,
            "failed": len(_results) - passed,
        },
        "overall": "PASS" if passed == len(_results) else "FAIL",
    }
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(record, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def target_dir():
    return ROOT / "data" / "target"


@pytest.fixture(scope="session")
def legacy_dir():
    return ROOT / "data" / "legacy"


def _jsonl(path):
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


@pytest.fixture(scope="session")
def audit(target_dir):
    return _jsonl(target_dir / "audit.jsonl")


@pytest.fixture(scope="session")
def quarantine(target_dir):
    return _jsonl(target_dir / "quarantine.jsonl")


@pytest.fixture(scope="session")
def run_summary(target_dir):
    return json.loads((target_dir / "run_summary.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def curated(target_dir):
    return {p.stem: _jsonl(p) for p in target_dir.glob("*.jsonl")
            if p.stem not in ("audit", "quarantine")}
