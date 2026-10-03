import json
import re

import pytest

from app.ml.common import ARTIFACTS_DIR
from scripts import validate

REPORT = validate.REPORT


@pytest.fixture(scope="module")
def result():
    return json.loads((ARTIFACTS_DIR / "validation.json").read_text(encoding="utf-8"))


def test_report_is_generated_and_matches_the_json(result):
    text = REPORT.read_text(encoding="utf-8")
    assert "Do not edit by hand" in text
    assert text == validate.render(result)  # the committed report is exactly what the code renders


def test_every_plan_target_is_checked(result):
    ids = {t["id"] for t in result["targets"]}
    assert {"T1-A", "T1-B", "T2-A", "T2-B"} <= ids
    assert {f"T3-{p}-{c}" for p in "AB" for c in ("gender", "region", "employer_size")} <= ids


def test_failed_targets_are_shown_as_failed_and_explanations_are_marked(result):
    text = REPORT.read_text(encoding="utf-8")
    for t in result["targets"]:
        row = next(line for line in text.splitlines() if line.startswith(f"| {t['id']} |"))
        if t["pass"]:
            assert "**PASS**" in row
        else:
            assert "FAIL" in row
            if t["id"] in validate.ANALYST_NOTES:
                assert "explained below" in row
    for key in validate.ANALYST_NOTES:
        assert any(t["id"] == key and not t["pass"] for t in result["targets"]), f"note for a target that did not fail: {key}"


def test_analyst_notes_contain_no_numbers_except_printed_labels():
    """Notes may refer to printed table labels (e.g. the 10-30% band) but must not introduce new results."""
    text = REPORT.read_text(encoding="utf-8").split("## Analyst notes")[0]
    for note in validate.ANALYST_NOTES.values():
        for number in re.findall(r"\d+(?:[.,]\d+)?%?", note):
            assert number in text or number.rstrip("%") in text, number


def test_policy_numbers_are_internally_consistent(result):
    for key in ("profile_a", "profile_b"):
        pol = result[key]["policies"]
        ml = pol["ml_tier"]
        assert sum(ml["tier_counts"].values()) == pol["n_advances"]
        assert ml["queued_for_human"] == ml["tier_counts"]["D"]
        assert ml["funds_deployed_bdt"] <= pol["flat_cap"]["funds_deployed_bdt"]
        assert abs(sum(v for v in result[key]["recovery_share_by_step_flat_cap"].values() if v) - 1) < 1e-3


@pytest.mark.slow
def test_validation_runs_end_to_end(tmp_path):
    out = validate.main(seeds=(42,), out=tmp_path / "v.json", report=tmp_path / "r.md")
    assert (tmp_path / "r.md").read_text(encoding="utf-8").startswith("# Validation Report")
    assert out["targets"]


def test_validation_endpoint_serves_the_generated_file(result):
    from fastapi.testclient import TestClient

    from app.main import app

    body = TestClient(app).get("/validation").json()
    assert body["targets"] == result["targets"]
    assert {"world_months", "advances_ml", "funds_ml_bdt", "loss_ml_bdt", "avg_days_outstanding"} <= set(body["profile_a"]["economics_base"])
    assert body["policy"]["fee_flat_bdt"] == 25
