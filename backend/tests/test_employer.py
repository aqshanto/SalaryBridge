import csv
import io

import pytest

H = {"X-Session-Id": "employer-test-0001"}
RISK_WORDS = ("risk", "tier", "prob", "shap", "score", "reason", "anomaly")


@pytest.fixture
def client(full_client):
    full_client.post("/sim/reset", headers=H)
    return full_client


@pytest.fixture(scope="module")
def personas(full_client):
    return {p["key"]: p for p in full_client.get("/personas", headers=H).json()}


def take(client, employee_id, amount):
    d = client.post("/advance/offer", json={"employee_id": employee_id, "amount_bdt": amount}, headers=H).json()
    assert d["status"] == "offered", d
    return client.post("/advance/accept", json={"decision_id": d["decision_id"]}, headers=H).json()


def keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from keys(v)


def test_employer_list_puts_persona_employers_first(client, personas):
    rows = client.get("/employer", headers=H).json()
    assert rows[0]["persona"] is not None
    assert {personas["rahim"]["employer_id"], personas["shapla"]["employer_id"]} <= {r["employer_id"] for r in rows if r["persona"]}


def test_notice_total_equals_open_advances_and_names_personas(client, personas):
    a = take(client, personas["rahim"]["employee_id"], 3_000)
    dash = client.get(f"/employer/{personas['rahim']['employer_id']}/dashboard", headers=H).json()
    assert dash["employees_with_deductions"] == 1
    assert dash["total_to_deduct_bdt"] == a["total_due_bdt"] == sum(i["amount_due_bdt"] for i in dash["notice"]["items"])
    assert dash["notice"]["items"][0]["name"] == "Rahim"
    assert dash["payday"] == a["due_date"]


def test_hr_responses_contain_no_risk_information(client, personas):
    take(client, personas["shapla"]["employee_id"], 2_000)  # tier C advance
    employer = personas["shapla"]["employer_id"]
    for path in (f"/employer/{employer}/dashboard", f"/employer/{employer}/deduction-notice", "/employer"):
        body = client.get(path, headers=H).json()
        leaked = [k for k in keys(body) if any(w in k.lower() for w in RISK_WORDS)]
        assert leaked == [], (path, leaked)
    text = client.get(f"/employer/{employer}/deduction-notice.csv", headers=H).text
    header = next(csv.reader(io.StringIO(text)))
    assert not [h for h in header if any(w in h.lower() for w in RISK_WORDS)]


def test_confirming_the_notice_records_the_total_without_moving_money(client, personas):
    take(client, personas["rahim"]["employee_id"], 2_000)
    employer = personas["rahim"]["employer_id"]
    pool = client.get("/sim/state", headers=H).json()["pool_bdt"]
    confirmed = client.post(f"/employer/{employer}/deduction-notice/confirm", headers=H).json()
    assert confirmed["confirmation"]["total_bdt"] == confirmed["total_bdt"]
    assert client.get("/sim/state", headers=H).json()["pool_bdt"] == pool
    empty = personas["karim"]["employer_id"]
    if empty != employer:
        assert client.post(f"/employer/{empty}/deduction-notice/confirm", headers=H).status_code == 409


def test_opting_out_pauses_advances_for_that_employers_staff(client, personas):
    employer = personas["rahim"]["employer_id"]
    r = client.put(f"/employer/{employer}/settings", json={"opted_in": False, "cap_pct": 20}, headers=H)
    assert r.json() == {"opted_in": False, "cap_pct": 20.0}
    d = client.post("/advance/offer", json={"employee_id": personas["rahim"]["employee_id"], "amount_bdt": 1_000}, headers=H).json()
    assert [x["code"] for x in d["reasons"] if x["direction"] == "decline"] == ["EMPLOYER_NOT_OPTED_IN"]


def test_employer_can_lower_but_not_raise_the_cap(client, personas):
    employer = personas["rahim"]["employer_id"]
    assert client.put(f"/employer/{employer}/settings", json={"opted_in": True, "cap_pct": 30}, headers=H).status_code == 400
    client.put(f"/employer/{employer}/settings", json={"opted_in": True, "cap_pct": 10}, headers=H)
    d = client.post("/advance/offer", json={"employee_id": personas["rahim"]["employee_id"], "amount_bdt": 5_000}, headers=H).json()
    assert d["hard_cap_bdt"] == 1_800  # 10% of 18,000
    summary = client.get(f"/employee/{personas['rahim']['employee_id']}/summary", headers=H).json()
    assert summary["available_bdt"] == 1_800


def test_unknown_employer_is_404(client):
    assert client.get("/employer/E999/dashboard", headers=H).status_code == 404
    assert client.put("/employer/E999/settings", json={"opted_in": True, "cap_pct": 10}, headers=H).status_code == 404
