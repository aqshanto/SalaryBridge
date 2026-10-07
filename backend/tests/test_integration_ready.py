import pytest

from app.integrations import payroll

H = {"X-Session-Id": "integration-test-0001"}


def test_csv_adapter_maps_hr_export_columns():
    rows = payroll.parse("Emp ID,Unpaid Leave Days,Net Pay\nA-1,2,\"18,000\"\nA-2,0,\n", "csv", "hr_export")
    assert rows == [payroll.PayrollRecord("A-1", 2, 18_000), payroll.PayrollRecord("A-2", 0, None)]


def test_json_adapter_and_validation_errors():
    assert payroll.parse('{"records": [{"staff_code": "X", "absent_unpaid": 3}]}', "json", "timecard")[0].unpaid_absent_days == 3
    for content, fmt in (("employee_id,unpaid_absent_days\nA,-1\n", "csv"), ("employee_id,unpaid_absent_days\nA,40\n", "csv"), ("[1, 2]", "json"), ("x", "xml")):
        with pytest.raises(payroll.PayrollFormatError):
            payroll.parse(content, fmt)


def test_payroll_import_endpoint_and_metrics(full_client):
    full_client.post("/sim/reset", headers=H)
    rahim = next(p for p in full_client.get("/personas", headers=H).json() if p["key"] == "rahim")
    csv_body = f"employee_id,unpaid_absent_days,net_salary_bdt\n{rahim['employee_id']},5,1\nSOMEONE-ELSE,1,\n"
    r = full_client.post(f"/employer/{rahim['employer_id']}/payroll-import?fmt=csv", content=csv_body, headers=H)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["accepted"] == 1 and out["unknown_staff"] == ["SOMEONE-ELSE"] and out["salary_mismatch_for_review"] == [rahim["employee_id"]]
    bad = full_client.post(f"/employer/{rahim['employer_id']}/payroll-import?fmt=csv", content="nope\n", headers=H)
    assert bad.status_code == 400
    full_client.post("/advance/offer", json={"employee_id": rahim["employee_id"], "amount_bdt": 1_000}, headers=H)
    text = full_client.get("/metrics").text
    assert 'route="/employer/{employer_id}/payroll-import"' in text and "salarybridge_decisions_total" in text
    assert rahim["employee_id"] not in text  # IDs never become metric labels
