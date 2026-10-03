import json

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine

from app.config import PolicyParams
from app.ml import m4_capital as m4
from app.ml.common import ARTIFACTS_DIR
from data.generator import generate

POLICY = PolicyParams()


@pytest.fixture(scope="module")
def panel():
    return m4.monthly_panel(generate("A", seed=42, scale=0.25))


def test_features_use_only_months_before_the_origin(panel):
    origin = 15
    series = panel[panel["employer_id"] == panel["employer_id"].iloc[0]]
    before = pd.DataFrame(m4._rows_for_series(series, [origin], panel.attrs["start"], panel.attrs["eid_months"], with_target=False))
    changed = series.copy()
    changed.loc[changed["month_index"] >= origin, ["amount", "requests", "headcount"]] = [10**7, 999, 1]
    after = pd.DataFrame(m4._rows_for_series(changed, [origin], panel.attrs["start"], panel.attrs["eid_months"], with_target=False))
    pd.testing.assert_frame_equal(before[m4.FEATURES + ["level", "headcount"]], after[m4.FEATURES + ["level", "headcount"]])


def test_forecast_scales_with_the_series_level(panel):
    """Doubling every amount doubles the forecast: the reason v2 works on busier worlds."""
    model = m4.load_model()
    wp = m4.world_panel(panel)
    rows = pd.DataFrame(m4._rows_for_series(wp, [15], panel.attrs["start"], panel.attrs["eid_months"], with_target=False))
    doubled = wp.assign(amount=wp["amount"] * 2)
    rows2 = pd.DataFrame(m4._rows_for_series(doubled, [15], panel.attrs["start"], panel.attrs["eid_months"], with_target=False))
    p1, p2 = model.world.predict(rows), model.world.predict(rows2)
    np.testing.assert_allclose(p2.to_numpy(), 2 * p1.to_numpy(), rtol=1e-9)


def test_quantiles_are_ordered_and_non_negative(panel):
    model = m4.load_model()
    rows = m4.build_rows(panel)
    pred = model.employer.predict(rows)
    assert (pred["p10"] <= pred["p50"]).all() and (pred["p50"] <= pred["p90"]).all()
    assert (pred >= 0).all().all()


def test_capital_forecast_from_seed_history(small_seed_url):
    engine = create_engine(small_seed_url)
    with engine.connect() as conn:
        tables = {t: pd.read_sql(f"select * from {t}", conn) for t in ("meta", "attendance", "employees", "advances", "advance_requests")}
    engine.dispose()
    panel = m4.monthly_panel(tables)
    out = m4.capital_forecast(panel, origin=panel.attrs["months"], policy=POLICY)
    assert out["origin_month"] == "2026-10"
    assert [m["month"] for m in out["months"]] == ["2026-10", "2026-11", "2026-12"]
    for m in out["months"]:
        assert m["p10_bdt"] <= m["p50_bdt"] <= m["p90_bdt"]
        assert abs(m["required_pool_bdt"] - m["p90_bdt"] * 1.15) <= 1
    assert {e["employer_id"] for e in out["per_employer"]} <= set(panel["employer_id"])


def test_committed_artifact_and_metrics_exist():
    assert (ARTIFACTS_DIR / f"{m4.MODEL_NAME}.joblib").exists()
    metrics = json.loads((ARTIFACTS_DIR / "metrics.json").read_text())[m4.MODEL_NAME]
    assert metrics["version"] == m4.VERSION
    for split in ("test_profile_a", "test_profile_b"):
        world = metrics[split]["world"]
        assert {"model", "previous_month", "seasonal_naive", "pool_covers_actual_share", "by_horizon"} <= set(world)
        assert "coverage_p10_p90" in world["model"]
    assert metrics["previous_version"]["version"] == "m4-v1"


def test_pinball_loss():
    y = np.array([10.0, 10.0])
    assert m4.pinball(y, np.array([10.0, 10.0]), 0.9) == 0
    assert m4.pinball(y, np.array([0.0, 0.0]), 0.9) == pytest.approx(9.0)
    assert m4.pinball(y, np.array([20.0, 20.0]), 0.9) == pytest.approx(1.0)


@pytest.mark.slow
def test_training_pipeline_runs_end_to_end(tmp_path):
    metrics = m4.train(seeds=(42, 1042, 2042), scale=0.5, artifacts_dir=tmp_path, eval_seeds_b=(42,))
    assert (tmp_path / f"{m4.MODEL_NAME}.joblib").exists()
    assert 0 <= metrics["test_profile_a"]["world"]["model"]["coverage_p10_p90"] <= 1
