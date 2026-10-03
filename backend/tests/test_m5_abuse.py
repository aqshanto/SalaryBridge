import json
from datetime import date

import pandas as pd
import pytest

from app.config import PolicyParams
from app.ml import m5_abuse as m5
from app.ml.common import ARTIFACTS_DIR
from app.rules.abuse import AbuseFlags, route
from app.rules.tiers import tier_for
from data.generator import generate

POLICY = PolicyParams()
AS_OF = date(2026, 7, 1)


@pytest.fixture(scope="module")
def world():
    return generate("A", seed=42, scale=0.25)


@pytest.fixture(scope="module")
def features(world):
    return m5.behaviour_features(world["advance_requests"], world["employees"], AS_OF)


def test_no_label_or_audit_attributes_are_features():
    assert set(m5.FEATURES).isdisjoint(m5.FORBIDDEN_FEATURES)


def test_features_ignore_requests_on_or_after_the_decision_date(world, features):
    req = world["advance_requests"].copy()
    later = pd.to_datetime(req["request_date"]) >= pd.Timestamp(AS_OF)
    req.loc[later, "amount_requested"] = 10**6
    extra = req[~later].head(50).assign(request_date=pd.Timestamp(AS_OF) + pd.Timedelta(days=3), request_id=lambda d: d["request_id"] + "X")
    again = m5.behaviour_features(pd.concat([req, extra]), world["employees"], AS_OF)
    pd.testing.assert_frame_equal(again, features)


def test_only_recent_borrowers_who_are_still_employed_get_a_row(world, features):
    people = world["employees"].set_index("employee_id")
    ended = people.loc[features["employee_id"], "end_date"]
    assert (ended.isna() | (pd.to_datetime(ended) >= pd.Timestamp(AS_OF))).all()
    assert (features["requests_6m"] >= 1).all()


def test_chronic_borrowers_have_long_streaks(world, features):
    behaviour = world["employees"].set_index("employee_id").loc[features["employee_id"], "behaviour"].to_numpy()
    streak = features["streak_months"].to_numpy()
    assert streak[behaviour == "chronic"].mean() > 3 * streak[behaviour == "normal"].mean()


def test_unusual_pattern_goes_to_a_person_and_chronic_is_coded():
    tier = tier_for(0.01, POLICY)
    decision, codes = route(tier, AbuseFlags(anomaly_score=0.7, unusual_pattern=True, chronic=False))
    assert decision.needs_human and codes == ["UNUSUAL_BORROWING_PATTERN"]
    assert decision.tier == "A"  # a flag does not decline or downgrade by itself
    decision, codes = route(tier, AbuseFlags(anomaly_score=0.4, unusual_pattern=False, chronic=True))
    assert not decision.needs_human and codes == ["CHRONIC_BORROWING"]


def test_flagged_rows_route_to_the_queue(features):
    flags = m5.abuse_check(features, POLICY)
    assert len(flags) == len(features)
    flagged = [f for f in flags if f.unusual_pattern]
    assert flagged, "expected at least one unusual pattern in a quarter-size world"
    for f in flagged:
        decision, _ = route(tier_for(0.01, POLICY), f)
        assert decision.needs_human
    chronic = [f for f, s in zip(flags, features["streak_months"]) if s >= POLICY.cooling_off_consecutive_months]
    assert all(f.chronic for f in chronic)


def test_committed_artifact_and_metrics_exist():
    assert (ARTIFACTS_DIR / f"{m5.MODEL_NAME}.joblib").exists()
    metrics = json.loads((ARTIFACTS_DIR / "metrics.json").read_text())[m5.MODEL_NAME]
    for split in ("test_profile_a", "test_profile_b"):
        rows = metrics[split]["mean_precision_at_k"]
        assert {r["k"] for r in rows} == set(m5.K_VALUES)
        assert {"model_any", "baseline_any", "model_abuser", "baseline_abuser"} <= set(rows[0])
    assert metrics["features"] == m5.FEATURES


@pytest.mark.slow
def test_training_pipeline_runs_end_to_end(tmp_path):
    metrics = m5.train(seeds=(42,), scale=0.5, artifacts_dir=tmp_path)
    assert (tmp_path / f"{m5.MODEL_NAME}.joblib").exists()
    assert metrics["test_profile_a"]["mean_precision_at_k"]
