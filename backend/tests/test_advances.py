import pandas as pd
import pytest

from app.config import get_settings
from app.sim.world import load_world

H = {"X-Session-Id": "advances-test-0001"}


@pytest.fixture
def client(full_client):
    full_client.post("/sim/reset", headers=H)
    return full_client


@pytest.fixture(scope="module")
def people(full_client):
    personas = {p["key"]: p for p in full_client.get("/personas", headers=H).json()}
    world = load_world(get_settings().database_url)
    emp = world.employees.reset_index(drop=True).merge(world.employers.reset_index(drop=True)[["employer_id", "reliability_type"]], on="employer_id")
    tenure = (pd.Timestamp("2026-10-20") - pd.to_datetime(emp["hire_date"])).dt.days
    recent = set(world.advances.loc[world.advances["work_month"] >= "2026-06", "employee_id"])
    big = emp[(emp["salary_bdt"] >= 60_000) & (tenure > 730) & (emp["reliability_type"] == "on_time") & (emp["behaviour"] == "normal") & ~emp["employee_id"].isin(recent)]
    return {**{k: p["employee_id"] for k, p in personas.items()}, "big_earner": big.sort_values("employee_id")["employee_id"].iloc[0]}


def offer(client, employee_id, amount):
    r = client.post("/advance/offer", json={"employee_id": employee_id, "amount_bdt": amount}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def state(client):
    return client.get("/sim/state", headers=H).json()


def accept(client, decision_id):
    return client.post("/advance/accept", json={"decision_id": decision_id}, headers=H)


def test_accepting_an_offer_moves_money_and_reconciles(client, people):
    before = float(state(client)["pool_bdt"])
    d = offer(client, people["rahim"], 5_000)
    assert d["status"] == "offered"
    r = accept(client, d["decision_id"])
    assert r.status_code == 200, r.text
    adv = r.json()
    assert adv["amount_bdt"] == d["approved_amount_bdt"] and adv["total_due_bdt"] == adv["amount_bdt"] + adv["fee_bdt"]
    assert float(adv["pool_bdt"]) == before - adv["amount_bdt"]
    assert float(adv["wallet_balance_bdt"]) == adv["amount_bdt"]
    assert adv["ledger_reconciled"] and state(client)["ledger"]["reconciled"]


def test_an_offer_can_be_accepted_only_once(client, people):
    d = offer(client, people["rahim"], 2_000)
    assert accept(client, d["decision_id"]).status_code == 200
    again = accept(client, d["decision_id"])
    assert again.status_code == 409 and "already accepted" in again.json()["detail"]


def test_accepted_advance_feeds_the_next_decision(client, people):
    first = offer(client, people["rahim"], 5_000)
    accept(client, first["decision_id"])
    second = offer(client, people["rahim"], 1_000)
    assert second["status"] == "declined"
    assert "CAP_BELOW_MINIMUM" in [r["code"] for r in second["reasons"] if r["direction"] == "decline"]


def test_offers_expire_when_time_moves_or_a_newer_advance_exists(client, people):
    stale = offer(client, people["rahim"], 1_000)
    client.post("/sim/advance-time", json={"days": 1}, headers=H)
    r = accept(client, stale["decision_id"])
    assert r.status_code == 409 and "expired" in r.json()["detail"]

    a, b = offer(client, people["rahim"], 1_000), offer(client, people["rahim"], 1_000)
    assert accept(client, a["decision_id"]).status_code == 200
    r = accept(client, b["decision_id"])
    assert r.status_code == 409 and "Another advance" in r.json()["detail"]


@pytest.mark.parametrize("bad", ["", "ok", "    "])
def test_queue_decisions_need_a_note(client, people, bad):
    d = offer(client, people["big_earner"], 10_000)
    assert d["status"] == "queued" and "LARGE_AMOUNT_REVIEW" in [r["code"] for r in d["reasons"]]
    for action in ("approve", "reject"):
        r = client.post(f"/ops/queue/{d['decision_id']}/{action}", json={"note": bad}, headers=H)
        assert r.status_code == 400 and "note" in r.json()["detail"]


def test_ops_approval_pays_out_and_leaves_the_queue(client, people):
    d = offer(client, people["big_earner"], 10_000)
    queued = client.get("/ops/queue", headers=H).json()
    item = next(q for q in queued if q["decision_id"] == d["decision_id"])
    assert item["risk"] and item["reasons"] and not item["expired"]
    r = client.post(f"/ops/queue/{d['decision_id']}/approve", json={"note": "Long tenure, reliable employer"}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["amount_bdt"] == 10_000 and r.json()["ledger_reconciled"]
    assert d["decision_id"] not in [q["decision_id"] for q in client.get("/ops/queue", headers=H).json()]
    log = client.get("/ops/reviews", headers=H).json()
    assert log[-1]["action"] == "approved" and log[-1]["note"] == "Long tenure, reliable employer"


def test_ops_can_approve_a_smaller_amount_but_not_above_the_cap(client, people):
    d = offer(client, people["big_earner"], 10_000)
    too_much = client.post(f"/ops/queue/{d['decision_id']}/approve", json={"note": "checked", "amount_bdt": d["hard_cap_bdt"] + 100}, headers=H)
    assert too_much.status_code == 400
    ok = client.post(f"/ops/queue/{d['decision_id']}/approve", json={"note": "checked", "amount_bdt": 6_000}, headers=H)
    assert ok.status_code == 200 and ok.json()["amount_bdt"] == 6_000


def test_rejection_moves_no_money(client, people):
    pool = state(client)["pool_bdt"]
    d = offer(client, people["big_earner"], 10_000)
    r = client.post(f"/ops/queue/{d['decision_id']}/reject", json={"note": "Needs a phone check first"}, headers=H)
    assert r.status_code == 200 and r.json()["action"] == "rejected"
    assert state(client)["pool_bdt"] == pool
    assert client.post(f"/ops/queue/{d['decision_id']}/approve", json={"note": "changed my mind"}, headers=H).status_code == 409


def test_wrong_kind_of_decision_is_refused(client, people):
    offered = offer(client, people["rahim"], 1_000)
    assert client.post(f"/ops/queue/{offered['decision_id']}/approve", json={"note": "not queued"}, headers=H).status_code == 409
    declined = offer(client, people["karim"], 1_000)
    assert accept(client, declined["decision_id"]).status_code == 409
    queued = offer(client, people["big_earner"], 10_000)
    assert accept(client, queued["decision_id"]).status_code == 409
    assert accept(client, "D999999").status_code == 400


def test_kill_switch_blocks_new_money_but_keeps_existing_advances(client, people):
    d = offer(client, people["rahim"], 2_000)
    accept(client, d["decision_id"])
    pending = offer(client, people["shapla"], 1_000)
    queued = offer(client, people["big_earner"], 10_000)

    assert client.post("/ops/kill-switch", json={"on": True}, headers=H).json() == {"kill_switch": True}
    blocked = offer(client, people["shapla"], 1_000)
    assert blocked["status"] == "declined" and [r["code"] for r in blocked["reasons"] if r["direction"] == "decline"] == ["KILL_SWITCH_ON"]
    assert accept(client, pending["decision_id"]).status_code == 409
    assert client.post(f"/ops/queue/{queued['decision_id']}/approve", json={"note": "fine by me"}, headers=H).status_code == 409
    # the advance paid before the switch still exists and the ledger is intact
    assert state(client)["ledger"]["reconciled"]

    client.post("/ops/kill-switch", json={"on": False}, headers=H)
    fresh = offer(client, people["shapla"], 1_000)
    assert fresh["status"] == "offered" and accept(client, fresh["decision_id"]).status_code == 200
