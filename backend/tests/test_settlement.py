import csv
import io
from datetime import date, timedelta

import pandas as pd
import pytest

from app.config import get_settings
from app.sim.session import SimSession
from app.sim.world import load_world

H = {"X-Session-Id": "settlement-test-0001"}
STEP_ORDER = ["employer_remittance", "wallet_debit", "carry_over", "write_off"]


@pytest.fixture
def client(full_client):
    full_client.post("/sim/reset", headers=H)
    return full_client


@pytest.fixture(scope="module")
def people(full_client):
    personas = {p["key"]: p for p in full_client.get("/personas", headers=H).json()}
    world = load_world(get_settings().database_url)
    staff = world.employees.reset_index(drop=True)
    rahim = staff.set_index("employee_id", drop=False).loc[personas["rahim"]["employee_id"]]
    recent = set(world.advances.loc[world.advances["work_month"] >= "2026-06", "employee_id"])
    tenure = (pd.Timestamp("2026-10-20") - pd.to_datetime(staff["hire_date"])).dt.days
    # A colleague of Rahim whose wages are NOT paid into upay (so the wallet cannot cover a shortfall).
    colleague = staff[
        (staff["employer_id"] == rahim["employer_id"])
        & ~staff["salary_to_upay"].astype(bool)
        & (tenure > 365)
        & (staff["behaviour"] == "normal")
        & ~staff["employee_id"].isin(recent)
    ].sort_values("employee_id")
    return {"rahim": rahim, "colleague": colleague.iloc[0], "colleague2": colleague.iloc[1]}


def session() -> SimSession:
    return SimSession(H["X-Session-Id"], get_settings(), load_world(get_settings().database_url)).open()


def take(client, employee_id, amount):
    d = client.post("/advance/offer", json={"employee_id": employee_id, "amount_bdt": amount}, headers=H).json()
    assert d["status"] == "offered", d
    a = client.post("/advance/accept", json={"decision_id": d["decision_id"]}, headers=H)
    assert a.status_code == 200, a.text
    return a.json()


def days(client, n):
    body = client.post("/sim/advance-time", json={"days": n}, headers=H).json()
    assert body["state"]["ledger"]["reconciled"], body["state"]["ledger"]
    return body


def steps(advance_id):
    return session().repayments_df(advance_id)


def assert_waterfall_order(advance_id):
    order = [STEP_ORDER.index(s) for s in steps(advance_id)["step"]]
    assert order == sorted(order)


def test_deduction_notice_is_one_list_one_total_without_risk(client, people):
    a = take(client, people["rahim"]["employee_id"], 5_000)
    b = take(client, people["colleague"]["employee_id"], 2_000)
    employer = people["rahim"]["employer_id"]
    notice = client.get(f"/employer/{employer}/deduction-notice", headers=H).json()
    assert notice["payday"] == a["due_date"]
    assert {i["advance_id"] for i in notice["items"]} == {a["advance_id"], b["advance_id"]}
    assert notice["total_bdt"] == a["total_due_bdt"] + b["total_due_bdt"]
    assert not {k for i in notice["items"] for k in i} & {"risk", "tier", "prob_fail", "prob_leave", "reasons"}

    text = client.get(f"/employer/{employer}/deduction-notice.csv", headers=H).text
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0][:2] == ["employee_id", "name"] and rows[-1] == ["TOTAL", "", "", "", "", str(notice["total_bdt"])]
    assert client.get("/employer/E999/deduction-notice", headers=H).status_code == 404


def test_s1_payday_remittance_recovers_and_wages_arrive_net(client, people):
    a = take(client, people["rahim"]["employee_id"], 5_000)
    start_pool = 5_000_000
    body = days(client, 15)  # 20 Oct -> 4 Nov, past the 1 Nov payday
    adv = session().advances_df(people["rahim"]["employee_id"]).iloc[0]
    assert adv["status"] == "recovered"
    assert list(steps(a["advance_id"])["step"]) == ["employer_remittance"]
    assert float(body["state"]["pool_bdt"]) == start_pool
    assert body["state"]["advances"]["fees_income_bdt"] == a["fee_bdt"]
    # Rahim is paid via upay: his wallet gets wages minus the deduction (the advance itself was spent).
    if bool(people["rahim"]["salary_to_upay"]):
        wallet = session().ledger.balance(f"employee_wallet:{people['rahim']['employee_id']}") / 100
        assert wallet == int(people["rahim"]["salary_bdt"]) - a["total_due_bdt"]


@pytest.mark.slow
def test_partial_payroll_uses_wallet_then_recovers(client, people):
    a = take(client, people["rahim"]["employee_id"], 5_000)
    session().force_payroll(people["rahim"]["employer_id"], "2026-10", "partial", 0, 0.5)
    days(client, 15)
    s = steps(a["advance_id"])
    assert list(s["step"])[:1] == ["employer_remittance"] and s["amount"].sum() == a["total_due_bdt"]
    if bool(people["rahim"]["salary_to_upay"]):
        assert "wallet_debit" in list(s["step"])
    assert_waterfall_order(a["advance_id"])


@pytest.mark.slow
def test_late_payroll_beyond_grace_recovers_but_misses_grace(client, people):
    a = take(client, people["rahim"]["employee_id"], 2_000)
    session().force_payroll(people["rahim"]["employer_id"], "2026-10", "late", 12, 1.0)
    days(client, 14)  # 3 Nov: payroll not paid yet, still on the notice
    notice_after_payday = client.get(f"/employer/{people['rahim']['employer_id']}/deduction-notice", headers=H).json()
    assert a["advance_id"] in [i["advance_id"] for i in notice_after_payday["items"]]
    days(client, 20)
    adv = session().advances_df(people["rahim"]["employee_id"]).iloc[0]
    assert adv["status"] == "recovered" and adv["recovered_by_grace"] == False  # noqa: E712
    assert str(steps(a["advance_id"])["date"].iloc[0]) == "2026-11-13"


@pytest.mark.slow
def test_s3_resignation_before_payday_ends_in_write_off_when_nothing_is_left(client, people):
    a = take(client, people["rahim"]["employee_id"], 5_000)
    s = session()
    s.resign(people["rahim"]["employee_id"], s.sim_date + timedelta(days=5))
    days(client, 6)
    blocked = client.post("/advance/offer", json={"employee_id": people["rahim"]["employee_id"], "amount_bdt": 1_000}, headers=H).json()
    assert blocked["status"] == "declined" and "EMPLOYEE_NOT_ACTIVE" in [r["code"] for r in blocked["reasons"]]
    body = days(client, 75)
    adv = session().advances_df(people["rahim"]["employee_id"]).iloc[0]
    st = body["state"]["advances"]
    assert adv["status"] in {"recovered", "written_off"}
    assert_waterfall_order(a["advance_id"])
    paid = steps(a["advance_id"])
    if adv["status"] == "written_off":
        lost_principal = a["amount_bdt"] - min(a["amount_bdt"], paid.loc[paid["step"] != "write_off", "amount"].sum())
        assert st["loss_provision_bdt"] == lost_principal > 0
        assert str(paid["date"].iloc[-1]) == (date.fromisoformat(a["due_date"]) + timedelta(days=get_settings().policy.writeoff_after_days)).isoformat()


@pytest.mark.slow
def test_s4_employer_default_closes_employer_and_writes_off(client, people):
    a = take(client, people["rahim"]["employee_id"], 5_000)
    session().force_payroll(people["rahim"]["employer_id"], "2026-10", "default", 0, 0.0)
    body = days(client, 15)
    assert people["rahim"]["employer_id"] in body["state"]["closed_employers"]
    colleague = client.post("/advance/offer", json={"employee_id": people["colleague"]["employee_id"], "amount_bdt": 1_000}, headers=H).json()
    assert colleague["status"] == "declined" and "EMPLOYER_CLOSED" in [r["code"] for r in colleague["reasons"]]
    body = days(client, 60)
    adv = session().advances_df(people["rahim"]["employee_id"]).iloc[0]
    assert adv["status"] == "written_off" and adv["recovered_by_grace"] == False  # noqa: E712
    assert body["state"]["advances"]["loss_provision_bdt"] == a["amount_bdt"]
    assert float(body["state"]["pool_bdt"]) == 5_000_000 - a["amount_bdt"]
    assert_waterfall_order(a["advance_id"])


@pytest.mark.slow
def test_carry_over_is_collected_next_payday_and_shrinks_the_next_limit(client, people):
    who = people["colleague"]
    a = take(client, who["employee_id"], 2_000)
    session().force_payroll(who["employer_id"], "2026-10", "partial", 0, 0.5)
    days(client, 15)  # 4 Nov: half remitted, wallet empty (wages not via upay) -> carried over
    adv = session().advances_df(who["employee_id"]).iloc[0]
    assert adv["status"] == "carried_over"
    days(client, 3)  # past grace: flagged as missed
    nxt = client.post("/advance/offer", json={"employee_id": people["colleague2"]["employee_id"], "amount_bdt": 1_000}, headers=H).json()
    assert nxt["status"] in {"offered", "queued", "declined"}
    again = client.post("/advance/offer", json={"employee_id": who["employee_id"], "amount_bdt": 1_000}, headers=H).json()
    assert "CARRY_OVER_REDUCTION" in [r["code"] for r in again["reasons"]]
    notice = client.get(f"/employer/{who['employer_id']}/deduction-notice", headers=H).json()
    assert any(i["advance_id"] == a["advance_id"] and i["kind"] == "carried_over" for i in notice["items"])
    days(client, 30)  # next payday 1 Dec
    s = steps(a["advance_id"])
    assert list(s["step"]) == ["employer_remittance", "carry_over"]
    assert s["amount"].sum() == a["total_due_bdt"]
    assert session().advances_df(who["employee_id"]).iloc[0]["status"] == "recovered"
