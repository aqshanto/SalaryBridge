import pytest

H = {"X-Session-Id": "ops-test-0001"}


@pytest.fixture
def client(full_client):
    full_client.post("/sim/reset", headers=H)
    return full_client


@pytest.fixture(scope="module")
def personas(full_client):
    return {p["key"]: p for p in full_client.get("/personas", headers=H).json()}


def test_kpis_follow_decisions_and_payouts(client, personas):
    empty = client.get("/ops/summary", headers=H).json()
    assert empty["decisions"] == 0 and empty["approval_rate"] is None and empty["loss_rate"] is None
    d = client.post("/advance/offer", json={"employee_id": personas["rahim"]["employee_id"], "amount_bdt": 3_000}, headers=H).json()
    client.post("/advance/accept", json={"decision_id": d["decision_id"]}, headers=H)
    client.post("/advance/offer", json={"employee_id": personas["karim"]["employee_id"], "amount_bdt": 1_000}, headers=H)  # declined
    k = client.get("/ops/summary", headers=H).json()
    assert k["decisions"] == 2 and k["approval_rate"] == 0.5
    assert k["outstanding_bdt"] == 3_025 and k["disbursed_bdt"] == 3_000
    assert k["pool_bdt"] == 5_000_000 - 3_000 and not k["pool_below_required"]
    assert k["loss_rate"] == 0.0


def test_kpis_show_the_pool_shortfall_in_the_eid_scenario(client):
    client.post("/sim/scenario", json={"name": "s6"}, headers=H)
    k = client.get("/ops/summary", headers=H).json()
    assert k["pool_below_required"] and k["pool_bdt"] < k["required_pool_bdt"]


def test_employer_table_has_trend_and_reasons(client):
    rows = client.get("/ops/employers", headers=H).json()
    assert rows == sorted(rows, key=lambda r: (-r["prob_late"], r["employer_id"]))
    for r in rows:
        assert abs(r["trend"] - (r["prob_late"] - r["prob_late_before"])) < 1e-3
        assert r["reasons"] and r["risk_source"] in {"m1", "rule"}


def test_borrowing_monitor_flags_about_the_configured_share(client):
    m = client.get("/ops/borrowing-monitor", headers=H).json()
    assert m["screened"] > 500
    assert 0.005 <= m["unusual"] / m["screened"] <= 0.05  # configured top 2% of training scores
    assert m["chronic"] > 0 and len(m["top"]) == 12
    scores = [t["anomaly_score"] for t in m["top"]]
    assert scores == sorted(scores, reverse=True)
    assert not {"gender", "region", "behaviour"} & set(m["top"][0])


def test_requests_log_lists_every_decision_and_filters_by_employee(full_client):
    h = {"X-Session-Id": "requests-test-0001"}
    full_client.post("/sim/reset", headers=h)
    people = full_client.get("/personas", headers=h).json()
    for p in people:
        full_client.post("/advance/offer", json={"employee_id": p["employee_id"], "amount_bdt": 1_000}, headers=h)
    rows = full_client.get("/ops/requests", headers=h).json()
    assert len(rows) == 3 and {r["name"] for r in rows} == {"Rahim", "Shapla", "Karim"}
    one = full_client.get(f"/ops/requests?employee_id={people[0]['employee_id']}", headers=h).json()
    assert len(one) == 1 and one[0]["employee_id"] == people[0]["employee_id"]
