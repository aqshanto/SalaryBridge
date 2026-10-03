import os
import time
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.sim.session import SimSession, cleanup_expired, sessions_dir
from app.sim.world import load_world

S1 = {"X-Session-Id": "session-one-0001"}
S2 = {"X-Session-Id": "session-two-0002"}


@pytest.fixture
def client(sim_env):
    return TestClient(app)


def test_new_session_starts_on_day_20_after_history_with_funded_pool(client, sim_env):
    state = client.get("/sim/state", headers=S1).json()
    first_live = load_world(sim_env.database_url).first_live_month
    assert state["sim_date"] == first_live.replace(day=20).isoformat()
    assert state["pool_bdt"] == f"{sim_env.policy.initial_pool_bdt}.00"
    assert state["ledger"]["reconciled"] is True


def test_missing_or_bad_session_id_is_rejected(client):
    assert client.get("/sim/state").status_code == 422
    assert client.get("/sim/state", headers={"X-Session-Id": "../../etc"}).status_code == 400


def test_sessions_are_isolated(client):
    start = client.get("/sim/state", headers=S2).json()["sim_date"]
    client.post("/sim/advance-time", json={"days": 15}, headers=S1)
    assert client.get("/sim/state", headers=S2).json()["sim_date"] == start
    assert client.get("/sim/state", headers=S1).json()["sim_date"] != start


def test_jump_to_payday_runs_payroll_and_ledger_reconciles(client):
    body = client.post("/sim/jump-to-payday", json={}, headers=S1).json()
    state = body["state"]
    assert date.fromisoformat(state["sim_date"]).day in {1, 5, 7}
    assert body["events"][-1]["date"] == state["sim_date"]
    assert any(e["type"] in ("payroll_scheduled", "payroll_default") for e in body["events"])
    assert state["ledger"]["reconciled"] is True


def test_jump_to_payday_for_one_employer(client, sim_env):
    employer = load_world(sim_env.database_url).employers.iloc[0]
    body = client.post("/sim/jump-to-payday", json={"employer_id": employer["employer_id"]}, headers=S1).json()
    assert date.fromisoformat(body["state"]["sim_date"]).day == int(employer["payroll_day"])
    assert client.post("/sim/jump-to-payday", json={"employer_id": "E999"}, headers=S1).status_code == 400


def test_45_days_schedules_every_open_employer_and_reconciles(client, sim_env):
    open_ids = set(load_world(sim_env.database_url).employers.index)
    body = client.post("/sim/advance-time", json={"days": 45}, headers=S1).json()
    events = body["events"]
    scheduled = [e for e in events if e["type"] in ("payroll_scheduled", "payroll_default")]
    assert {e["employer_id"] for e in scheduled} == open_ids
    for e in scheduled:
        if e["status"] == "late":
            assert e["actual_date"] > e["date"]
    paid = {e["employer_id"] for e in events if e["type"] == "payroll_paid"}
    expected_paid = {e["employer_id"] for e in scheduled if e["actual_date"] and e["actual_date"] <= body["state"]["sim_date"]}
    assert paid == expected_paid
    assert body["state"]["ledger"]["reconciled"] is True
    assert body["state"]["ledger"]["total_paisa"] == 0


def test_reset_restores_initial_state(client):
    initial = client.post("/sim/reset", headers=S1).json()
    client.post("/sim/advance-time", json={"days": 30}, headers=S1)
    again = client.post("/sim/reset", headers=S1).json()
    assert again == initial


def test_same_seed_gives_same_payroll_in_every_session(client):
    a = client.post("/sim/advance-time", json={"days": 20}, headers=S1).json()["events"]
    b = client.post("/sim/advance-time", json={"days": 20}, headers=S2).json()["events"]
    assert a == b


def test_advance_time_limits(client):
    assert client.post("/sim/advance-time", json={"days": 0}, headers=S1).status_code == 422
    assert client.post("/sim/advance-time", json={"days": 500}, headers=S1).status_code == 422


def test_expired_sessions_are_cleaned_up(sim_env):
    world = load_world(sim_env.database_url)
    old = SimSession("old-session-0001", sim_env, world).open()
    old.engine.dispose()
    past = time.time() - (sim_env.session_ttl_hours + 1) * 3600
    os.utime(old.path, (past, past))
    assert cleanup_expired(sim_env) == 1
    assert not (sessions_dir(sim_env) / "old-session-0001.db").exists()
