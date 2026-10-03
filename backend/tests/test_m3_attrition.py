import json

import numpy as np
import pandas as pd
import pytest

from app.config import PolicyParams
from app.ml import m1_employer as m1
from app.ml import m2_repayment as m2
from app.ml import m3_attrition as m3
from app.ml.common import ARTIFACTS_DIR
from app.rules.tiers import apply_attrition, tier_for
from data.generator import generate

POLICY = PolicyParams()


@pytest.fixture(scope="module")
def world():
    return generate("A", seed=42, scale=0.25)


@pytest.fixture(scope="module")
def data(world):
    return m3.build_dataset(world, m1.load_model(), POLICY.grace_days)


def test_no_audit_or_hidden_attributes_are_features():
    assert set(m3.FEATURES).isdisjoint(m3.FORBIDDEN_FEATURES)
    assert "attendance" not in " ".join(m3.FEATURES)


def test_target_counts_only_leaving_before_payday(data):
    positives = data[data["target"] == 1]
    assert len(positives) > 0
    assert set(positives["end_reason"]) <= set(m3.LEAVING_REASONS)
    end, issue, due = (pd.to_datetime(positives[c]) for c in ("end_date", "issue_date", "due_date"))
    assert ((end > issue) & (end < due)).all()
    closed = data[data["end_reason"] == "employer_closed"]
    assert (closed["target"] == 0).all()


def test_requested_to_cap_uses_only_the_request(data):
    full = data[data["amount_requested"] >= data["hard_cap"]]
    assert (full["requested_to_cap"] >= 1).all()


@pytest.mark.parametrize(
    "fail_risk, leave_risk, expected_tier, downgraded",
    [
        (0.01, 0.10, "A", False),
        (0.01, 0.30, "B", True),
        (0.10, 0.50, "C", True),
        (0.20, 0.50, "D", True),
        (0.90, 0.90, "D", False),
    ],
)
def test_attrition_downgrade_rule(fail_risk, leave_risk, expected_tier, downgraded):
    decision, changed = apply_attrition(tier_for(fail_risk, POLICY), leave_risk, POLICY)
    assert (decision.tier, changed) == (expected_tier, downgraded)
    assert decision.needs_human == (expected_tier == "D")
    assert decision.risk == fail_risk


def test_combined_tiers_never_raise_a_tier():
    rng = np.random.default_rng(1)
    p_fail, p_leave = rng.random(500) * 0.4, rng.random(500)
    tiers, _ = m3.combined_tiers(p_fail, p_leave, POLICY)
    order = "ABCD"
    for pf, t in zip(p_fail, tiers):
        assert order.index(t.tier) >= order.index(tier_for(float(pf), POLICY).tier)


def test_committed_artifact_and_metrics_exist():
    assert (ARTIFACTS_DIR / f"{m3.MODEL_NAME}.joblib").exists()
    metrics = json.loads((ARTIFACTS_DIR / "metrics.json").read_text())[m3.MODEL_NAME]
    for split in ("test_profile_a", "test_profile_b"):
        e = metrics[split]
        assert {"model", "baseline", "downgraded_by_attrition", "policy_m2_only", "policy_m2_plus_m3"} <= set(e)
        assert "loss_rate_m3" in e["policy_m2_plus_m3"]["equal_approval"][0]
    assert metrics["features"] == m3.FEATURES


def test_inference_returns_probability_and_reasons(data):
    results = m3.attrition_risk(data.head(20))
    codes = {code for pair in m3.REASONS.values() for code in pair}
    for r in results:
        assert 0 <= r["prob_leave"] <= 1
        assert 1 <= len(r["reasons"]) <= 3 and all(x["code"] in codes for x in r["reasons"])


def test_m2_policy_keys_unchanged_after_refactor(data):
    m2_model = m2.load_model()
    out = m2.policy_comparison(data, m2_model.predict_proba(data), m2_model.baseline_proba(data), POLICY)
    assert set(out["equal_approval"][0]) == {"decline_share", "loss_rate_ml", "loss_rate_tenure_rule", "loss_rate_random"}


@pytest.mark.slow
def test_training_pipeline_runs_end_to_end(tmp_path):
    m1.train(seeds=(42,), scale=0.5, artifacts_dir=tmp_path)
    m2.train(seeds=(42,), scale=0.5, artifacts_dir=tmp_path)
    metrics = m3.train(seeds=(42,), scale=0.5, artifacts_dir=tmp_path)
    assert (tmp_path / f"{m3.MODEL_NAME}.joblib").exists()
    assert np.isfinite(metrics["test_profile_a"]["model"]["brier"])
