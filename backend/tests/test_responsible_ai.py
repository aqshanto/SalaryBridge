"""Responsible-AI and security controls that are not already covered elsewhere.

The full control list, with the test that guards each one, is in docs/responsible_ai.md.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.ml import m1_employer as m1
from app.ml import m2_repayment as m2
from app.ml import m3_attrition as m3
from app.ml import m4_capital as m4
from app.ml import m5_abuse as m5

AUDIT_ONLY = {"gender", "region", "age_band"}
HIDDEN = {"behaviour", "reliability_type", "end_date", "end_reason", "closed_month_index"}
H = {"X-Session-Id": "rai-test-0001"}


# ---------- 1. No audit or hidden attributes inside any saved model ----------
@pytest.mark.parametrize(
    "name, features",
    [
        ("m1", lambda: m1.load_model().booster.feature_name_),
        ("m2", lambda: m2.load_model().booster.feature_name_),
        ("m3", lambda: m3.load_model().booster.feature_name_),
        ("m4", lambda: m4.FEATURES),
        ("m5", lambda: m5.load_model().features),
    ],
)
def test_saved_models_never_use_audit_or_hidden_attributes(name, features):
    used = set(features())
    assert used, name
    assert not used & (AUDIT_ONLY | HIDDEN), (name, used & (AUDIT_ONLY | HIDDEN))


def test_saved_booster_features_match_the_declared_lists():
    assert list(m1.load_model().booster.feature_name_) == m1.FEATURES
    assert list(m2.load_model().booster.feature_name_) == m2.FEATURES
    assert list(m3.load_model().booster.feature_name_) == m3.FEATURES


# ---------- 2. CORS allow-list ----------
def test_cors_allows_only_configured_origins():
    client = TestClient(app)
    ok = client.options("/health", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:3000"
    bad = client.options("/health", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in bad.headers


# ---------- 3. A model can never say "no": high risk goes to a person ----------
class _AlwaysRisky:
    version = "test-risky"

    def __init__(self, p: float):
        self.p = p

    def predict_proba(self, X):
        import numpy as np

        return np.full(len(X), self.p)

    def reasons(self, X, top=3):
        return [[] for _ in range(len(X))]


@pytest.mark.parametrize("p_fail, p_leave", [(0.9, 0.01), (0.2, 0.95), (0.99, 0.99)])
def test_extreme_model_risk_queues_for_a_person_and_never_declines(full_client, monkeypatch, p_fail, p_leave):
    monkeypatch.setattr(m2, "load_model", lambda *a, **k: _AlwaysRisky(p_fail))
    monkeypatch.setattr(m3, "load_model", lambda *a, **k: _AlwaysRisky(p_leave))
    full_client.post("/sim/reset", headers=H)
    rahim = next(p for p in full_client.get("/personas", headers=H).json() if p["key"] == "rahim")
    d = full_client.post("/advance/offer", json={"employee_id": rahim["employee_id"], "amount_bdt": 2_000}, headers=H).json()
    assert d["status"] == "queued" and d["needs_human"] is True
    assert not [r for r in d["reasons"] if r["direction"] == "decline"]
    assert d["decision_id"] in [q["decision_id"] for q in full_client.get("/ops/queue", headers=H).json()]


# ---------- 4. Input validation ----------
@pytest.mark.parametrize(
    "body",
    [
        {"employee_id": "E005-0227-01037", "amount_bdt": 0},
        {"employee_id": "E005-0227-01037", "amount_bdt": -500},
        {"employee_id": "E005-0227-01037", "amount_bdt": 5_000_000},
        {"employee_id": "x" * 200, "amount_bdt": 1_000},
        {"employee_id": "E005-0227-01037"},
    ],
)
def test_offer_rejects_bad_input(full_client, body):
    r = full_client.post("/advance/offer", json=body, headers=H)
    assert r.status_code == 422


@pytest.mark.parametrize("path", ["/employer/..%2F..%2Fetc/dashboard", "/employer/E001'%20OR%201=1/settings", "/employee/%00/summary"])
def test_odd_identifiers_are_404_not_500(full_client, path):
    full_client.post("/sim/reset", headers=H)
    assert full_client.get(path, headers=H).status_code in (400, 404)


# ---------- 5. The controls document cannot drift from the tests ----------
def test_doc_references_existing_tests():
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    doc = (root.parent / "docs" / "responsible_ai.md").read_text(encoding="utf-8")
    refs = set(re.findall(r"`(tests/test_\w+\.py)::(test_\w+)`", doc))
    assert len(refs) >= 25
    missing = [f"{f}::{t}" for f, t in sorted(refs) if f"def {t}(" not in (root / f).read_text(encoding="utf-8")]
    assert not missing, missing
