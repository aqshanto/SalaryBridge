from fastapi.testclient import TestClient

from app.config import APP_VERSION, get_settings
from app.main import app


def test_health_returns_status_version_seed():
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "version": APP_VERSION, "seed": get_settings().seed}
