import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.sim.personas import personas_for
from app.sim.world import load_world
from data.seed import write_seed_db

CACHES = (get_settings, load_world, personas_for)


@pytest.fixture(scope="session")
def small_seed_url(tmp_path_factory):
    """A small (5%) seed world shared by API tests."""
    path = tmp_path_factory.mktemp("seed") / "seed.db"
    url = f"sqlite:///{path.as_posix()}"
    write_seed_db(url, seed=42, scale=0.05)
    return url


@pytest.fixture(scope="session")
def full_seed_url(tmp_path_factory):
    """The full-size seed world (needed wherever the demo personas must exist)."""
    path = tmp_path_factory.mktemp("full") / "seed.db"
    url = f"sqlite:///{path.as_posix()}"
    write_seed_db(url, seed=42)
    return url


@pytest.fixture
def sim_env(small_seed_url, tmp_path, monkeypatch):
    """Point the app at the small seed and a private sessions folder."""
    monkeypatch.setenv("DATABASE_URL", small_seed_url)
    monkeypatch.setenv("SIM_DIR", str(tmp_path / "sessions"))
    for cached in CACHES:
        cached.cache_clear()
    yield get_settings()
    for cached in CACHES:
        cached.cache_clear()


@pytest.fixture(scope="module")
def full_client(full_seed_url, tmp_path_factory):
    """A TestClient on the full seed. Module scope: the world and personas load once per test file."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATABASE_URL", full_seed_url)
        mp.setenv("SIM_DIR", str(tmp_path_factory.mktemp("sessions")))
        for cached in CACHES:
            cached.cache_clear()
        yield TestClient(app)
    for cached in CACHES:
        cached.cache_clear()
