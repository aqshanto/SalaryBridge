"""One test per plot.md §4 scenario, asserting the 'expected system behaviour' column."""

import pytest

from app.config import get_settings
from app.sim.session import SimSession
from app.sim.world import load_world

H = {"X-Session-Id": "scenario-test-0001"}


def scenario(client, name, **extra):
    r = client.post("/sim/scenario", json={"name": name, **extra}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def offer(client, employee_id, amount):
    r = client.post("/advance/offer", json={"employee_id": employee_id, "amount_bdt": amount}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def accept(client, decision_id):
    r = client.post("/advance/accept", json={"decision_id": decision_id}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def days(client, n):
    body = client.post("/sim/advance-time", json={"days": n}, headers=H).json()
    assert body["state"]["ledger"]["reconciled"]
    return body


def session():
    return SimSession(H["X-Session-Id"], get_settings(), load_world(get_settings().database_url)).open()


def declines(d):
    return [r["code"] for r in d["reasons"] if r["direction"] == "decline"]


def test_unknown_scenario_is_a_400(full_client):
    r = full_client.post("/sim/scenario", json={"name": "s9"}, headers=H)
    assert r.status_code == 400 and "s1_happy_path" in r.json()["detail"]


def test_s1_happy_path(full_client):
    s = scenario(full_client, "s1")
    assert s["scenario"] == "s1_happy_path" and s["persona"]["key"] == "rahim"
    d = offer(full_client, s["persona"]["employee_id"], 5_000)
    assert d["status"] == "offered"
    adv = accept(full_client, d["decision_id"])
    body = full_client.post("/sim/jump-to-payday", json={"employer_id": s["persona"]["employer_id"]}, headers=H).json()
    assert body["state"]["ledger"]["reconciled"]
    paid = session().repayments_df(adv["advance_id"])
    assert list(paid["step"]) == ["employer_remittance"] and paid["amount"].sum() == adv["total_due_bdt"]


@pytest.mark.slow
def test_s2_risky_employer_smaller_limit_recovered_late_within_grace(full_client):
    s = scenario(full_client, "s2")
    d = offer(full_client, s["persona"]["employee_id"], 10_000)
    assert d["status"] == "offered" and d["max_amount_bdt"] < d["hard_cap_bdt"]
    assert any(r["code"].startswith("EMPLOYER_") and r["direction"] == "raises_risk" for r in d["reasons"])
    adv = accept(full_client, d["decision_id"])
    days(full_client, 20)
    row = session().advances_df(s["persona"]["employee_id"]).iloc[0]
    paid = session().repayments_df(adv["advance_id"])
    assert row["status"] == "recovered" and row["recovered_by_grace"] == True  # noqa: E712
    assert str(paid["date"].iloc[0]) > adv["due_date"]  # late, but inside the grace period


@pytest.mark.slow
def test_s3_resigns_before_payday_counts_the_loss(full_client):
    s = scenario(full_client, "s3")
    days(full_client, get_settings().policy.writeoff_after_days + 20)
    sess = session()
    row = sess.advances_df(s["persona"]["employee_id"]).iloc[0]
    steps = list(sess.repayments_df(s["advance_id"])["step"])
    order = ["employer_remittance", "wallet_debit", "carry_over", "write_off"]
    assert [order.index(x) for x in steps] == sorted(order.index(x) for x in steps)
    assert "carry_over" not in steps  # someone who left cannot carry over
    state = sess.state()["advances"]
    if row["status"] == "written_off":
        assert state["loss_provision_bdt"] > 0
    else:
        assert row["status"] == "recovered"


@pytest.mark.slow
def test_s4_employer_default_raises_risk_pauses_staff_and_shows_exposure(full_client):
    s = scenario(full_client, "s4")
    employer = s["employer_id"]

    def row():
        return next(e for e in full_client.get("/ops/employers", headers=H).json() if e["employer_id"] == employer)

    before = row()
    assert before["exposure_bdt"] > 0 and before["status"] == "open"
    assert before["risk_source"] == "m1"
    full_client.post("/sim/jump-to-payday", json={"employer_id": employer}, headers=H)
    after = row()
    # A default is a known fact: shown by rule at 100%, not left to the model.
    assert after["status"] == "closed" and after["prob_late"] == 1.0 > before["prob_late"]
    assert after["risk_source"] == "rule" and after["reasons"][0]["code"] == "EMPLOYER_DEFAULTED"
    assert after["exposure_bdt"] == before["exposure_bdt"]  # still owed until written off
    colleague = load_world(get_settings().database_url).employees.query("employer_id == @employer").index
    other = next(e for e in colleague if e != s["persona"]["employee_id"])
    assert "EMPLOYER_CLOSED" in declines(offer(full_client, other, 1_000))
    days(full_client, get_settings().policy.writeoff_after_days + 5)
    assert row()["exposure_bdt"] == 0
    assert session().state()["advances"]["loss_provision_bdt"] > 0


def test_s5_chronic_borrower_gets_a_supportive_pause(full_client):
    s = scenario(full_client, "s5")
    d = offer(full_client, s["persona"]["employee_id"], 3_000)
    assert d["status"] == "declined" and declines(d) == ["COOLING_OFF"]
    supportive = next(r for r in d["reasons"] if r["code"] == "CHRONIC_BORROWING")
    assert "protects" in supportive["detail"]


def test_s6_eid_surge_raises_the_band_and_queues_requests(full_client):
    s = scenario(full_client, "s6")
    assert s["required_after_bdt"] > s["required_before_bdt"] >= s["pool_bdt"]
    cap = full_client.get("/ops/capital-forecast", headers=H).json()
    assert cap["pool_below_required"] and cap["months"][0]["is_eid"]
    d = offer(full_client, s["persona"]["employee_id"], 5_000)
    assert d["status"] == "queued" and "POOL_BELOW_FORECAST" in [r["code"] for r in d["reasons"]]
    assert d["risk"]["required_pool_bdt"] == cap["current_required_pool_bdt"]


def test_s7_kill_switch_blocks_new_requests_but_existing_advances_settle(full_client):
    s = scenario(full_client, "s7")
    assert s["state"]["kill_switch"] is True
    d = offer(full_client, s["persona"]["employee_id"], 1_000)
    assert d["status"] == "declined" and declines(d) == ["KILL_SWITCH_ON"]
    rahim_employer = session().advances_df().iloc[0]["employer_id"]
    full_client.post("/sim/jump-to-payday", json={"employer_id": rahim_employer}, headers=H)
    assert list(session().repayments_df(s["advance_id"])["step"])[:1] == ["employer_remittance"]


def test_reset_false_keeps_the_current_state(full_client):
    scenario(full_client, "s7")
    s = scenario(full_client, "s5", reset=False)
    assert s["state"]["kill_switch"] is True  # S7's kill switch is still on
