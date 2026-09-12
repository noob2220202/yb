import os

import pytest


@pytest.fixture(scope="module")
def api_client(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("api") / "test_api.db"
    original_env = {
        k: os.environ.get(k)
        for k in ("DATABASE_URL", "USE_DEMO_PROVIDER", "API_KEYS", "POLL_INTERVAL_SECONDS", "POLL_SPORTS")
    }
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    os.environ["USE_DEMO_PROVIDER"] = "true"
    os.environ["API_KEYS"] = "test-key"
    os.environ["POLL_INTERVAL_SECONDS"] = "3600"
    os.environ["POLL_SPORTS"] = "soccer,basketball"

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


def test_health_needs_no_auth(api_client):
    resp = api_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_opportunities_requires_api_key(api_client):
    resp = api_client.get("/opportunities")
    assert resp.status_code in (401, 422)  # missing header -> FastAPI 422, wrong key -> 401


def test_opportunities_rejects_wrong_key(api_client):
    resp = api_client.get("/opportunities", headers={"x-api-key": "wrong"})
    assert resp.status_code == 401


def test_opportunities_lists_demo_arbitrage(api_client):
    resp = api_client.get("/opportunities", headers={"x-api-key": "test-key"})
    assert resp.status_code == 200
    body = resp.json()
    # seeded by the one startup poll against DemoProvider (soccer 3-way ML,
    # soccer totals 2.5, soccer AH -0.5, basketball 2-way ML)
    assert len(body) == 4
    assert all(o["margin_percent"] > 0 for o in body)


def test_stake_plan_computes_guaranteed_profit(api_client):
    opportunities = api_client.get("/opportunities", headers={"x-api-key": "test-key"}).json()
    opp_id = opportunities[0]["id"]

    resp = api_client.get(
        f"/opportunities/{opp_id}/stake-plan",
        params={"total_stake": 1000},
        headers={"x-api-key": "test-key"},
    )
    assert resp.status_code == 200
    plan = resp.json()
    assert plan["guaranteed_profit"] > 0
    assert sum(leg["stake"] for leg in plan["legs"]) == pytest.approx(1000, abs=1)


def test_stake_plan_404_for_unknown_opportunity(api_client):
    resp = api_client.get(
        "/opportunities/999999/stake-plan",
        params={"total_stake": 100},
        headers={"x-api-key": "test-key"},
    )
    assert resp.status_code == 404


def test_value_edges_lists_demo_edge(api_client):
    resp = api_client.get("/value-edges", headers={"x-api-key": "test-key"})
    assert resp.status_code == 200
    body = resp.json()
    assert any(e["selection"] == "2-1" for e in body)


def test_admin_poll_triggers_another_cycle(api_client):
    resp = api_client.post("/admin/poll", headers={"x-api-key": "test-key"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["opportunities_found"] == 4

    # Now two cycles' worth should be stored.
    resp = api_client.get("/opportunities", headers={"x-api-key": "test-key"}, params={"limit": 500})
    assert len(resp.json()) == 8
