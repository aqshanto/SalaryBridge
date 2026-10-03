import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.ml import m1_employer as m1
from app.ml.common import ARTIFACTS_DIR
from data.generator import generate

GRACE = 5


@pytest.fixture(scope="module")
def world():
    return generate("A", seed=42, scale=0.25)


def _employer_with_late_history(world):
    runs = world["payroll_runs"]
    late_ids = runs.loc[runs["status"].isin(["late", "partial"]), "employer_id"]
    employer_id = late_ids.value_counts().index[0]
    employer = world["employers"].set_index("employer_id", drop=False).loc[employer_id]
    return employer, runs[runs["employer_id"] == employer_id].copy()


def test_no_hidden_or_audit_attributes_are_features():
    assert set(m1.FEATURES).isdisjoint(m1.FORBIDDEN_FEATURES)


def test_features_ignore_everything_after_the_decision_date(world):
    employer, runs = _employer_with_late_history(world)
    as_of = date(2025, 9, 20)
    before = m1.employer_features(employer, runs, as_of, GRACE)

    future = runs.copy()
    after = pd.to_datetime(future["scheduled_date"]).dt.date >= as_of
    future.loc[after, ["status", "delay_days", "paid_share"]] = ["default", 30, 0.0]
    assert m1.employer_features(employer, future, as_of, GRACE) == before
    assert m1.employer_features(employer, runs[~after], as_of, GRACE) == before


def test_unpaid_run_counts_only_its_delay_so_far(world):
    employer, runs = _employer_with_late_history(world)
    run = runs.iloc[5]
    scheduled = pd.Timestamp(run["scheduled_date"]).date()
    as_of = scheduled + pd.Timedelta(days=3)
    edited = runs.copy()
    edited.loc[run.name, ["status", "delay_days", "actual_date"]] = ["late", 15, scheduled + pd.Timedelta(days=15)]
    obs = m1.observed_runs(edited, as_of.date() if hasattr(as_of, "date") else as_of, GRACE)
    assert obs.iloc[-1]["obs_delay"] == 3
    assert obs.iloc[-1]["obs_late"] == 0  # 3 days late is still within grace on that date
    # changing the eventual delay (unknown on as_of) must not change the features
    edited2 = edited.copy()
    edited2.loc[run.name, ["delay_days", "actual_date"]] = [25, scheduled + pd.Timedelta(days=25)]
    a = as_of.date() if hasattr(as_of, "date") else as_of
    assert m1.employer_features(employer, edited, a, GRACE) == m1.employer_features(employer, edited2, a, GRACE)


def test_employer_whose_only_past_run_defaulted(world):
    employer = world["employers"].iloc[0]
    runs = pd.DataFrame(
        [{"employer_id": employer["employer_id"], "scheduled_date": date(2025, 1, 1), "actual_date": None, "status": "default", "delay_days": 0, "paid_share": 0.0}]
    )
    row = m1.employer_features(employer, runs, date(2025, 1, 20), GRACE)
    assert row["last_late_beyond_grace"] == 1 and row["last_delay_days"] == 19


def test_target_is_that_months_run(world):
    data = m1.build_dataset(world, GRACE)
    runs = world["payroll_runs"].set_index(["employer_id", "month_index"])
    for row in data.sample(30, random_state=0).itertuples():
        run = runs.loc[(row.employer_id, row.month_index)]
        assert row.target == int(m1.is_late_beyond_grace(run["status"], run["delay_days"], GRACE))


def test_committed_artifact_and_metrics_exist():
    assert (ARTIFACTS_DIR / f"{m1.MODEL_NAME}.joblib").exists()
    metrics = json.loads((ARTIFACTS_DIR / "metrics.json").read_text())[m1.MODEL_NAME]
    for split in ("test_profile_a", "test_profile_b"):
        for who in ("model", "baseline"):
            assert {"pr_auc", "brier", "calibration"} <= set(metrics[split][who])
    assert metrics["features"] == m1.FEATURES


def test_inference_returns_probability_and_reasons(world):
    employer, runs = _employer_with_late_history(world)
    result = m1.employer_risk(employer, runs, date(2026, 8, 20))
    assert 0 <= result["prob_late"] <= 1
    assert 1 <= len(result["reasons"]) <= 3
    codes = {code for pair in m1.REASONS.values() for code in pair}
    assert all(r["code"] in codes for r in result["reasons"])
    assert result["model_version"] == m1.VERSION


def test_risk_is_higher_after_recent_late_payroll(world):
    employer, runs = _employer_with_late_history(world)
    as_of = date(2026, 8, 20)
    calm = runs.copy()
    calm[["status", "delay_days", "paid_share"]] = ["on_time", 0, 1.0]
    calm["actual_date"] = calm["scheduled_date"]
    stormy = calm.copy()
    recent = pd.to_datetime(stormy["scheduled_date"]).dt.date < as_of
    idx = stormy[recent].index[-3:]
    stormy.loc[idx, ["status", "delay_days"]] = ["late", 12]
    stormy.loc[idx, "actual_date"] = pd.to_datetime(stormy.loc[idx, "scheduled_date"]) + pd.Timedelta(days=12)
    p_calm = m1.employer_risk(employer, calm, as_of)["prob_late"]
    p_stormy = m1.employer_risk(employer, stormy, as_of)["prob_late"]
    assert p_stormy > p_calm


@pytest.mark.slow
def test_training_pipeline_runs_end_to_end(tmp_path):
    metrics = m1.train(seeds=(42,), scale=0.5, artifacts_dir=tmp_path)
    assert (tmp_path / f"{m1.MODEL_NAME}.joblib").exists()
    assert json.loads((tmp_path / "metrics.json").read_text())[m1.MODEL_NAME]["version"] == m1.VERSION
    assert np.isfinite(metrics["test_profile_a"]["model"]["brier"])
