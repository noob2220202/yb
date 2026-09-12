import os

import pytest


@pytest.fixture(scope="module")
def api_client(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("api") / "test_api.db"
    original_env = {
        k: os.environ.get(k) for k in ("DATABASE_URL", "API_KEYS", "POLL_INTERVAL_SECONDS", "POLL_SPORTS")
    }
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    os.environ["API_KEYS"] = "test-key"
    os.environ["POLL_INTERVAL_SECONDS"] = "3600"
    os.environ["POLL_SPORTS"] = "soccer,basketball"

    from app.config import get_settings
    from app.db.session import get_engine, get_session_maker

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_maker.cache_clear()

    # No real Pinnacle/Odds API credentials in tests -- swap in a fixture
    # provider for the duration of this module so the API has real
    # (fixture) data to serve, without the app itself ever knowing about
    # dummy data. This mirrors exactly how a real deployment would behave
    # once PINNACLE_USERNAME/ODDS_API_KEY etc. are actually configured.
    import app.scheduler as scheduler_module
    from tests.fixtures import FixtureProvider

    original_build_providers = scheduler_module.build_providers
    scheduler_module.build_providers = lambda: [FixtureProvider()]

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        yield client

    scheduler_module.build_providers = original_build_providers
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


def test_opportunities_lists_fixture_arbitrage(api_client):
    resp = api_client.get("/opportunities", headers={"x-api-key": "test-key"})
    assert resp.status_code == 200
    body = resp.json()
    # seeded by the one startup poll against the fixture provider (soccer
    # 3-way ML, soccer totals 2.5, soccer AH -0.5, basketball 2-way ML)
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


def test_value_edges_lists_fixture_edge(api_client):
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


# ---------------------------------------------------------------------
# /calculator/arbitrage: manual odds entry, no DB/scanner involved.
# ---------------------------------------------------------------------


def test_calculator_requires_api_key(api_client):
    resp = api_client.post(
        "/calculator/arbitrage",
        json={
            "market": "moneyline_2way",
            "total_stake": 1000,
            "legs": [
                {"selection": "home", "bookmaker": "A", "decimal_odds": 2.10},
                {"selection": "away", "bookmaker": "B", "decimal_odds": 2.10},
            ],
        },
    )
    assert resp.status_code in (401, 422)


def test_calculator_finds_genuine_two_way_arbitrage(api_client):
    resp = api_client.post(
        "/calculator/arbitrage",
        headers={"x-api-key": "test-key"},
        json={
            "market": "moneyline_2way",
            "total_stake": 100000,
            "legs": [
                {"selection": "home", "bookmaker": "북메이커A", "decimal_odds": 2.10},
                {"selection": "away", "bookmaker": "북메이커B", "decimal_odds": 2.10},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_arbitrage"] is True
    assert body["guaranteed_profit"] > 0
    assert sum(leg["stake"] for leg in body["legs"]) == pytest.approx(100000, abs=1)
    # Both sides pay out (approximately) the same amount regardless of outcome.
    payouts = [leg["payout"] for leg in body["legs"]]
    assert max(payouts) - min(payouts) < 1.0


def test_calculator_reports_negative_margin_instead_of_erroring(api_client):
    resp = api_client.post(
        "/calculator/arbitrage",
        headers={"x-api-key": "test-key"},
        json={
            "market": "moneyline_2way",
            "total_stake": 1000,
            "legs": [
                {"selection": "home", "bookmaker": "A", "decimal_odds": 1.80},
                {"selection": "away", "bookmaker": "B", "decimal_odds": 1.80},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_arbitrage"] is False
    assert body["guaranteed_profit"] < 0


def test_calculator_handles_quarter_line_asian_handicap(api_client):
    resp = api_client.post(
        "/calculator/arbitrage",
        headers={"x-api-key": "test-key"},
        json={
            "market": "asian_handicap",
            "line": -0.25,
            "total_stake": 100000,
            "legs": [
                {"selection": "home", "bookmaker": "A", "decimal_odds": 2.20},
                {"selection": "away", "bookmaker": "B", "decimal_odds": 2.20},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["quarter_line"] is True
    assert body["push_possible"] is False
    assert body["is_arbitrage"] is True


def test_calculator_rejects_missing_line_for_totals(api_client):
    resp = api_client.post(
        "/calculator/arbitrage",
        headers={"x-api-key": "test-key"},
        json={
            "market": "totals",
            "total_stake": 1000,
            "legs": [
                {"selection": "over", "bookmaker": "A", "decimal_odds": 2.10},
                {"selection": "under", "bookmaker": "B", "decimal_odds": 2.10},
            ],
        },
    )
    assert resp.status_code == 400


def test_calculator_rejects_missing_selection(api_client):
    resp = api_client.post(
        "/calculator/arbitrage",
        headers={"x-api-key": "test-key"},
        json={
            "market": "moneyline_3way",
            "total_stake": 1000,
            "legs": [
                {"selection": "home", "bookmaker": "A", "decimal_odds": 2.10},
                {"selection": "away", "bookmaker": "B", "decimal_odds": 2.10},
            ],
        },
    )
    assert resp.status_code == 400


def test_calculator_rejects_unknown_market(api_client):
    resp = api_client.post(
        "/calculator/arbitrage",
        headers={"x-api-key": "test-key"},
        json={
            "market": "not_a_real_market",
            "total_stake": 1000,
            "legs": [{"selection": "home", "bookmaker": "A", "decimal_odds": 2.10}],
        },
    )
    assert resp.status_code == 400
