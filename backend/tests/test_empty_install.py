"""Verifies the core guarantee of removing dummy data from the app: with
no real provider configured, the app must show nothing -- never
fabricated picks. Kept in its own file (rather than test_api.py) so this
test's real, empty ``build_providers()`` isn't affected by test_api.py's
module-scoped fixture patching that function to a fixture provider for
its own tests -- pytest tears down a module's fixtures only after every
test in that module finishes, so sharing a module would risk this test
running while that patch is still in effect.
"""

import os

import pytest


@pytest.fixture
def empty_api_client(tmp_path):
    db_path = tmp_path / "empty.db"
    original_env = {
        k: os.environ.get(k)
        for k in ("DATABASE_URL", "API_KEYS", "POLL_INTERVAL_SECONDS", "PINNACLE_USERNAME", "ODDS_API_KEY")
    }
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    os.environ["API_KEYS"] = "empty-key"
    os.environ["POLL_INTERVAL_SECONDS"] = "3600"
    os.environ.pop("PINNACLE_USERNAME", None)
    os.environ.pop("ODDS_API_KEY", None)

    from app.config import get_settings
    from app.db.session import get_engine, get_session_maker

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_maker.cache_clear()

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        yield client

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_maker.cache_clear()
    for k, v in original_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_fresh_install_with_no_providers_configured_is_empty_not_fake(empty_api_client):
    resp = empty_api_client.get("/opportunities", headers={"x-api-key": "empty-key"})
    assert resp.status_code == 200
    assert resp.json() == []

    resp = empty_api_client.get("/value-edges", headers={"x-api-key": "empty-key"})
    assert resp.status_code == 200
    assert resp.json() == []

    resp = empty_api_client.post("/admin/poll", headers={"x-api-key": "empty-key"})
    assert resp.status_code == 200
    assert resp.json() == {"opportunities_found": 0, "value_edges_found": 0, "parlay_finds_found": 0}

    resp = empty_api_client.get("/parlay-value", headers={"x-api-key": "empty-key"})
    assert resp.status_code == 200
    assert resp.json() == []
