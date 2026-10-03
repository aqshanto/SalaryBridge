from dataclasses import asdict

import pytest

from data import checks
from data.generator import PROFILES, generate
from data.report import build_report

DECLARED_DIFFERENCES = (
    "reliability_mix",
    "late_prob",
    "late_persistence",
    "late_window_start_day",
    "eid_months",
    "attrition_short_tenure",
    "chronic_share",
    "abuser_share",
    "salary_scale",
)


@pytest.fixture(scope="module")
def worlds():
    return {name: generate(name, seed=42) for name in ("A", "B")}


def test_profile_b_parameters_differ_from_a():
    a, b = asdict(PROFILES["A"]), asdict(PROFILES["B"])
    for key in DECLARED_DIFFERENCES:
        assert a[key] != b[key], key


@pytest.mark.slow
def test_profile_b_world_is_harsher(worlds):
    a, b = worlds["A"], worlds["B"]
    assert b["employees"]["salary_bdt"].median() < a["employees"]["salary_bdt"].median()
    assert (b["payroll_runs"]["status"] != "on_time").mean() > (a["payroll_runs"]["status"] != "on_time").mean()
    assert checks.outcome_summary(b)["failure_rate"] > checks.outcome_summary(a)["failure_rate"]
    assert (b["employers"]["reliability_type"] == "default_risk").sum() > (a["employers"]["reliability_type"] == "default_risk").sum()


@pytest.mark.slow
def test_profile_b_patterns_hold(worlds):
    failed = [c for c in checks.pattern_checks(worlds["B"], PROFILES["B"]) if not c.passed]
    assert not failed, failed


def test_profile_b_is_deterministic():
    x, y = generate("B", seed=5, scale=0.1), generate("B", seed=5, scale=0.1)
    assert x["advances"].equals(y["advances"])


@pytest.mark.slow
def test_report_is_generated_from_code():
    report = build_report(seed=42)
    assert "Do not edit by hand" in report
    assert "## 4. Pattern checks" in report
    assert "FAIL" not in report
