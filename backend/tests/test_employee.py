import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.sim.session import SimSession, sim_decisions
from app.sim.world import load_world

H = {"X-Session-Id": "employee-test-0001"}


@pytest.fixture
def client(full_client):
    full_client.post("/sim/reset", headers=H)
    return full_client


@pytest.fixture(scope="module")
def personas(full_client):
    return {p["key"]: p for p in full_client.get("/personas", headers=H).json()}


def summary(client, employee_id):
    r = client.get(f"/employee/{employee_id}/summary", headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def decisions_logged() -> int:
    s = SimSession(H["X-Session-Id"], get_settings(), load_world(get_settings().database_url)).open()
    with s.engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(sim_decisions)).scalar_one()


def test_rahim_home_card_matches_plot(client, personas):
    s = summary(client, personas["rahim"]["employee_id"])
    assert s["available_bdt"] == 3_600 and s["preview_status"] == "offered"
    assert s["earned_to_date_bdt"] == int(s["salary_bdt"] * s["days_worked"] / s["days_in_month"])
    assert s["next_deduction_date"] == "2026-11-01" and s["fee_bdt"] == 25


def test_preview_is_not_logged_and_never_reaches_the_queue(client, personas):
    before = decisions_logged()
    for p in personas.values():
        summary(client, p["employee_id"])
    assert decisions_logged() == before
    assert client.get("/ops/queue", headers=H).json() == []


def test_paused_person_sees_zero_and_a_message(client, personas):
    s = summary(client, personas["karim"]["employee_id"])
    assert s["available_bdt"] == 0 and s["preview_status"] == "declined"
    assert "pause" in s["preview_message"]["en"] and s["preview_message"]["bn"]
    assert s["history"] and all(h["source"] == "history" for h in s["history"])


def test_history_and_wallet_update_after_accepting(client, personas):
    eid = personas["rahim"]["employee_id"]
    d = client.post("/advance/offer", json={"employee_id": eid, "amount_bdt": 2_000}, headers=H).json()
    client.post("/advance/accept", json={"decision_id": d["decision_id"]}, headers=H)
    s = summary(client, eid)
    assert s["wallet_bdt"] == "2000.00"
    assert s["history"][0]["source"] == "live" and s["history"][0]["status"] == "open"
    assert s["available_bdt"] == 1_600  # 3,600 cap minus 2,000 still owed
    client.post("/sim/jump-to-payday", json={"employer_id": personas["rahim"]["employer_id"]}, headers=H)
    after = summary(client, eid)["history"][0]
    assert after["status"] == "recovered" and after["settled_via"] == ["payday"]


def test_unknown_employee_is_404(client):
    assert client.get("/employee/NOPE/summary", headers=H).status_code == 404


def test_public_config_says_whether_ai_wording_is_available(client):
    assert client.get("/config/public").json()["llm_enabled"] is False
