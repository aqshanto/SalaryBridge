import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.config import PolicyParams
from app.ml import m1_employer as m1
from app.ml import m2_repayment as m2
from app.ml.common import ARTIFACTS_DIR
from app.rules.tiers import tier_for, tiered_amount
from data.generator import generate

POLICY = PolicyParams()


@pytest.fixture(scope="module")
def world():
    return generate("A", seed=42, scale=0.25)


@pytest.fixture(scope="module")
def data(world):
    return m2.build_dataset(world, m1.load_model(), POLICY.grace_days)


def test_no_audit_or_hidden_attributes_are_features():
    assert set(m2.FEATURES).isdisjoint(m2.FORBIDDEN_FEATURES)


@pytest.mark.parametrize(
    "risk, tier, share, human",
    [(0.0, "A", 1.0, False), (0.05, "A", 1.0, False), (0.0501, "B", 0.7, False), (0.12, "B", 0.7, False), (0.25, "C", 0.4, False), (0.2501, "D", 0.0, True)],
)
def test_tier_boundaries(risk, tier, share, human):
    t = tier_for(risk, POLICY)
    assert (t.tier, t.share_of_cap, t.needs_human) == (tier, share, human)


def test_tiered_amount_only_shrinks_the_hard_cap():
    assert tiered_amount(3_600, 5_000, tier_for(0.01, POLICY), POLICY) == 3_600
    assert tiered_amount(3_600, 5_000, tier_for(0.10, POLICY), POLICY) == 2_500  # 70% of 3,600 = 2,520 -> 2,500
    assert tiered_amount(3_600, 1_000, tier_for(0.10, POLICY), POLICY) == 1_000
    assert tiered_amount(1_000, 1_000, tier_for(0.20, POLICY), POLICY) == 0  # 40% = 400 < minimum 500
    assert tiered_amount(3_600, 1_000, tier_for(0.90, POLICY), POLICY) == 0  # tier D goes to a person


def _employee_history(world):
    adv = world["advances"]
    busiest = adv["employee_id"].value_counts().index[0]
    rows = adv[adv["employee_id"] == busiest].sort_values("issue_date").reset_index(drop=True)
    rows["employer_prob_late"] = 0.1
    return rows


def test_features_ignore_later_advances_and_unknown_outcomes(world):
    rows = _employee_history(world)
    target = rows.iloc[5]
    base = m2.compute_features(rows, world["employees"], world["employers"], POLICY.grace_days)
    expected = base[base["advance_id"] == target["advance_id"]].reset_index(drop=True)

    later = rows["issue_date"] > target["issue_date"]
    changed = rows.copy()
    changed.loc[later, "amount"] = 99_999
    changed.loc[later, "recovered_by_grace"] = ~changed.loc[later, "recovered_by_grace"].astype(bool)
    # also flip outcomes whose grace period had not ended by the target's issue date
    grace_end = pd.to_datetime(changed["due_date"]) + pd.Timedelta(days=POLICY.grace_days)
    unknown = (~later) & (grace_end >= pd.Timestamp(target["issue_date"])) & (changed["advance_id"] != target["advance_id"])
    changed.loc[unknown, "recovered_by_grace"] = ~changed.loc[unknown, "recovered_by_grace"].astype(bool)

    again = m2.compute_features(changed, world["employees"], world["employers"], POLICY.grace_days)
    got = again[again["advance_id"] == target["advance_id"]].reset_index(drop=True)
    pd.testing.assert_frame_equal(got, expected)


def test_live_request_with_unknown_outcome_gets_features(world):
    rows = _employee_history(world)
    last = rows.iloc[-1]
    live = rows.copy()
    live.loc[len(live)] = {**last.to_dict(), "advance_id": "LIVE", "issue_date": pd.Timestamp(last["issue_date"]) + pd.Timedelta(days=40), "recovered_by_grace": np.nan}
    feats = m2.compute_features(live, world["employees"], world["employers"], POLICY.grace_days)
    row = feats[feats["advance_id"] == "LIVE"].iloc[0]
    assert row["prior_advances"] == len(rows)


def test_employer_snapshot_is_never_after_issue_date(data, world):
    snap = m2._snapshot_date(world["advances"]["issue_date"])
    assert (pd.to_datetime(snap) <= pd.to_datetime(world["advances"]["issue_date"])).all()
    assert data["employer_prob_late"].notna().all()


def test_committed_artifact_and_policy_metrics_exist():
    assert (ARTIFACTS_DIR / f"{m2.MODEL_NAME}.joblib").exists()
    metrics = json.loads((ARTIFACTS_DIR / "metrics.json").read_text())[m2.MODEL_NAME]
    for split in ("test_profile_a", "test_profile_b"):
        policy = metrics[split]["policy"]
        assert {"flat_cap", "ml_tiers", "equal_approval"} <= set(policy)
        assert {"loss_rate_ml", "loss_rate_tenure_rule", "loss_rate_random"} <= set(policy["equal_approval"][0])
    assert metrics["features"] == m2.FEATURES


def test_inference_returns_tier_and_reasons(data):
    rows = data.head(20)
    results = m2.repayment_risk(rows, POLICY)
    codes = {code for pair in m2.REASONS.values() for code in pair}
    for r in results:
        assert 0 <= r["prob_fail"] <= 1
        assert r["tier"] in {"A", "B", "C", "D"}
        assert r["needs_human"] == (r["tier"] == "D")
        assert 1 <= len(r["reasons"]) <= 3 and all(x["code"] in codes for x in r["reasons"])
        assert len({x["code"] for x in r["reasons"]}) == len(r["reasons"])  # no duplicate codes


@pytest.mark.slow
def test_training_pipeline_runs_end_to_end(tmp_path):
    m1.train(seeds=(42,), scale=0.5, artifacts_dir=tmp_path)
    metrics = m2.train(seeds=(42,), scale=0.5, artifacts_dir=tmp_path)
    assert (tmp_path / f"{m2.MODEL_NAME}.joblib").exists()
    assert np.isfinite(metrics["test_profile_a"]["model"]["brier"])
