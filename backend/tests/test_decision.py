import json

import pytest
from sqlalchemy import create_engine, insert, select

from app.config import get_settings
from app.sim.session import sim_decisions, sim_meta

H = {"X-Session-Id": "decision-test-0001"}


@pytest.fixture
def client(full_client):
    full_client.post("/sim/reset", headers=H)
    return full_client


@pytest.fixture(scope="module")
def personas(full_client):
    return {p["key"]: p for p in full_client.get("/personas", headers=H).json()}


def offer(client, employee_id, amount):
    r = client.post("/advance/offer", json={"employee_id": employee_id, "amount_bdt": amount}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def codes(d, source=None):
    return [r["code"] for r in d["reasons"] if source is None or r["source"] == source]


def test_personas_match_plot(personas):
    assert set(personas) == {"rahim", "shapla", "karim"}
    assert personas["rahim"]["industry"] == "garments"


def test_s1_rahim_happy_path_is_offered(client, personas):
    d = offer(client, personas["rahim"]["employee_id"], 5_000)
    assert d["status"] == "offered", d
    assert d["tier"] in {"A", "B"}
    assert 0 < d["approved_amount_bdt"] <= d["hard_cap_bdt"] == 3_600
    assert d["total_due_bdt"] == d["approved_amount_bdt"] + d["fee_bdt"]
    assert "SALARY_CAP_LIMIT" in codes(d, "rule")
    assert set(d["model_versions"]) == {"m1", "m2", "m3", "m5"}


def test_asking_for_the_full_limit_is_not_treated_as_leaving(client, personas):
    """Regression for the F12 finding: the full limit alone must not look like an abuser."""
    d = offer(client, personas["rahim"]["employee_id"], 3_600)
    assert d["risk"]["prob_leave"] < get_settings().policy.attrition_downgrade_threshold
    assert "ATTRITION_DOWNGRADE" not in codes(d)


def test_s2_risky_employer_gets_a_smaller_limit_with_employer_reasons(client, personas):
    d = offer(client, personas["shapla"]["employee_id"], 10_000)
    assert d["status"] in {"offered", "queued"}
    assert d["max_amount_bdt"] < d["hard_cap_bdt"]
    assert d["risk"]["employer_prob_late"] > 0.1
    employer_codes = [r for r in d["reasons"] if r["code"].startswith("EMPLOYER_") and r["direction"] == "raises_risk"]
    assert employer_codes, d["reasons"]


def test_s5_chronic_borrower_is_paused_with_a_supportive_reason(client, personas):
    d = offer(client, personas["karim"]["employee_id"], 3_000)
    assert d["status"] == "declined" and d["approved_amount_bdt"] == 0 and d["fee_bdt"] == 0
    assert "COOLING_OFF" in codes(d) and "CHRONIC_BORROWING" in codes(d)


def test_only_rules_can_decline(client, personas):
    for p in personas.values():
        for amount in (500, 2_000, 5_000, 20_000):
            d = offer(client, p["employee_id"], amount)
            if d["status"] == "declined":
                assert all(r["source"] == "rule" for r in d["reasons"] if r["direction"] == "decline")
            else:
                assert not [r for r in d["reasons"] if r["direction"] == "decline"]
            assert d["approved_amount_bdt"] <= max(d["hard_cap_bdt"], 0) or d["status"] == "declined"


def test_kill_switch_declines_new_offers(client, personas):
    session_db = get_settings().sim_dir
    engine = create_engine(f"sqlite:///{session_db}/{H['X-Session-Id']}.db")
    with engine.begin() as conn:
        conn.execute(insert(sim_meta).values(key="kill_switch", value="on"))
    engine.dispose()
    d = offer(client, personas["rahim"]["employee_id"], 2_000)
    assert d["status"] == "declined"
    assert [r["code"] for r in d["reasons"] if r["direction"] == "decline"] == ["KILL_SWITCH_ON"]


def test_every_decision_is_logged_with_inputs_and_versions(client, personas):
    d = offer(client, personas["rahim"]["employee_id"], 2_000)
    engine = create_engine(f"sqlite:///{get_settings().sim_dir}/{H['X-Session-Id']}.db")
    with engine.connect() as conn:
        row = conn.execute(select(sim_decisions).where(sim_decisions.c.decision_id == d["decision_id"])).mappings().one()
    engine.dispose()
    payload = json.loads(row["payload"])
    assert row["status"] == d["status"] and row["approved_bdt"] == d["approved_amount_bdt"]
    assert {"rule_result", "rule_trace", "m1_features", "m2_m3_features"} <= set(payload["inputs"])
    assert payload["output"]["model_versions"] == d["model_versions"]
    assert "gender" not in payload["inputs"]["m2_m3_features"] and "region" not in payload["inputs"]["m2_m3_features"]


def test_unknown_employee_is_a_400(client):
    r = client.post("/advance/offer", json={"employee_id": "NOPE", "amount_bdt": 1_000}, headers=H)
    assert r.status_code == 400


def test_offer_after_a_live_payday_works(client, personas):
    """Regression: seed (text) and live (date) payroll dates used to crash M1 feature sorting."""
    client.post("/sim/advance-time", json={"days": 15}, headers=H)
    d = offer(client, personas["rahim"]["employee_id"], 1_000)
    assert d["status"] in {"offered", "queued"} and d["sim_date"] == "2026-11-04"
