import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import PolicyParams, Settings
from app.main import app

ASSUMPTIONS_DOC = Path(__file__).resolve().parents[2] / "docs" / "assumptions.md"
ROW = re.compile(r"^\|\s*`(?P<key>\w+)`\s*\|\s*(?P<default>[^|]+?)\s*\|")


def _doc_defaults() -> dict[str, str]:
    rows = (ROW.match(line) for line in ASSUMPTIONS_DOC.read_text(encoding="utf-8").splitlines())
    return {m["key"]: m["default"] for m in rows if m}


def test_public_config_returns_every_policy_key():
    body = TestClient(app).get("/config/public").json()
    assert set(body["policy"]) == set(PolicyParams.model_fields)
    assert "seed" in body


def test_public_config_has_no_secrets():
    body = TestClient(app).get("/config/public").json()
    assert "anthropic_api_key" not in str(body)


def test_assumptions_doc_lists_every_policy_key_with_matching_default():
    doc = _doc_defaults()
    defaults = PolicyParams().model_dump()
    assert set(doc) == set(defaults), "assumptions.md and PolicyParams keys differ"
    for key, value in defaults.items():
        assert float(doc[key]) == float(value), f"{key}: doc {doc[key]} != code {value}"


def test_policy_can_be_overridden_by_env(monkeypatch):
    monkeypatch.setenv("POLICY__CAP_PCT_OF_SALARY", "25")
    assert Settings().policy.cap_pct_of_salary == 25.0
