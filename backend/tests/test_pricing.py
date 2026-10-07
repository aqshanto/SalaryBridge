import json

from app.config import get_settings
from app.ml.common import ARTIFACTS_DIR
from app.services.pricing import SCENARIOS, band_of, evaluate, scenario_table


def _base():
    return {
        "world_months": 1,
        "advances_ml": 100,
        "funds_ml_bdt": 300_000,
        "loss_ml_bdt": 2_000,
        "avg_days_outstanding": 15,
        "advances_by_band": {"upto_2000": 50, "upto_5000": 40, "above_5000": 10},
        "upay_payroll_employees": 400,
    }


def test_bands():
    assert band_of(2_000) == "upto_2000" and band_of(2_001) == "upto_5000" and band_of(9_000) == "above_5000"


def test_revenue_mix_adds_up():
    policy = get_settings().policy
    mix = next(s for s in SCENARIOS if s.key == "recommended_mix")
    r = evaluate(_base(), policy, mix)
    worker = 50 * 15 + 40 * 25 + 10 * 40
    assert r["revenue"] == {"worker": worker, "employer_copay": 100 * 20, "payroll_fee": 400 * 10, "total": worker + 2_000 + 4_000}
    assert r["net_bdt_per_month"] == r["revenue"]["total"] - r["costs"]["total"]


def test_break_even_scenario_is_viable_and_copay_needed_is_consistent():
    policy = get_settings().policy
    table = scenario_table(_base(), policy)
    by_key = {s["key"]: s for s in table["scenarios"]}
    assert by_key["worker_pays_break_even"]["viable"]
    current = by_key["current_flat_25"]
    assert current["copay_needed_to_break_even_bdt"] * 100 + current["revenue"]["worker"] >= current["costs"]["total"]


def test_generated_report_has_a_viable_recommended_mix_in_both_worlds():
    data = json.loads((ARTIFACTS_DIR / "validation.json").read_text(encoding="utf-8"))
    for profile in ("profile_a", "profile_b"):
        by_key = {s["key"]: s for s in data[profile]["pricing"]["scenarios"]}
        assert not by_key["current_flat_25"]["viable"]
        assert by_key["recommended_mix"]["viable"]
        assert by_key["recommended_mix"]["avg_worker_fee_bdt"] <= 25  # workers do not pay more than today on average
