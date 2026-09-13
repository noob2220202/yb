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
# /calculator/scan: manual pool of odds across many markets at once,
# no DB/scanner involved.
# ---------------------------------------------------------------------


def test_scan_requires_api_key(api_client):
    resp = api_client.post(
        "/calculator/scan",
        json={
            "total_stake": 1000,
            "legs": [
                {"market": "moneyline_2way", "selection": "home", "bookmaker": "A", "decimal_odds": 2.10},
                {"market": "moneyline_2way", "selection": "away", "bookmaker": "B", "decimal_odds": 2.10},
            ],
        },
    )
    assert resp.status_code in (401, 422)


def test_scan_finds_verified_arbitrage_among_a_mixed_pool(api_client):
    """The core ask: throw in odds across several markets at once, only
    ONE of which is actually a genuine arbitrage, and get it picked out."""
    resp = api_client.post(
        "/calculator/scan",
        headers={"x-api-key": "test-key"},
        json={
            "total_stake": 100000,
            "legs": [
                # Genuine 2-way arbitrage.
                {"market": "moneyline_2way", "selection": "home", "bookmaker": "북메이커A", "decimal_odds": 2.10},
                {"market": "moneyline_2way", "selection": "away", "bookmaker": "북메이커B", "decimal_odds": 2.10},
                # Ordinary vig'd totals -- not an arbitrage.
                {"market": "totals", "line": 2.5, "selection": "over", "bookmaker": "C", "decimal_odds": 1.85},
                {"market": "totals", "line": 2.5, "selection": "under", "bookmaker": "C", "decimal_odds": 1.85},
                # BTTS with only one side entered -- incomplete, unverifiable.
                {"market": "btts", "selection": "yes", "bookmaker": "D", "decimal_odds": 1.90},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()["groups"]

    ml = next(g for g in body if g["market"] == "moneyline_2way")
    assert ml["verified"] is True
    assert ml["is_arbitrage"] is True
    assert ml["guaranteed_profit"] > 0
    assert ml["warning"] is None

    totals = next(g for g in body if g["market"] == "totals")
    assert totals["verified"] is True
    assert totals["is_arbitrage"] is False

    # BTTS never appears as a group at all -- a single leg can't be scanned.
    assert not any(g["market"] == "btts" for g in body)

    # The genuine arbitrage sorts first.
    assert body[0]["market"] == "moneyline_2way"


def test_scan_handles_quarter_line_asian_handicap(api_client):
    resp = api_client.post(
        "/calculator/scan",
        headers={"x-api-key": "test-key"},
        json={
            "total_stake": 100000,
            "legs": [
                {"market": "asian_handicap", "line": -0.25, "selection": "home", "bookmaker": "A", "decimal_odds": 2.20},
                {"market": "asian_handicap", "line": -0.25, "selection": "away", "bookmaker": "B", "decimal_odds": 2.20},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()["groups"]
    assert len(body) == 1
    assert body[0]["quarter_line"] is True
    assert body[0]["push_possible"] is False
    assert body[0]["is_arbitrage"] is True
    assert body[0]["verified"] is True


def test_scan_verifies_european_handicap_as_a_3way_clean_partition(api_client):
    resp = api_client.post(
        "/calculator/scan",
        headers={"x-api-key": "test-key"},
        json={
            "total_stake": 100000,
            "legs": [
                {"market": "european_handicap", "line": -1, "selection": "home", "bookmaker": "A", "decimal_odds": 3.30},
                {"market": "european_handicap", "line": -1, "selection": "draw", "bookmaker": "B", "decimal_odds": 3.60},
                {"market": "european_handicap", "line": -1, "selection": "away", "bookmaker": "C", "decimal_odds": 3.30},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()["groups"]
    assert len(body) == 1
    assert body[0]["verified"] is True
    assert body[0]["is_arbitrage"] is True
    assert len(body[0]["legs"]) == 3


def test_scan_marks_missing_selection_as_unverified_with_a_warning(api_client):
    resp = api_client.post(
        "/calculator/scan",
        headers={"x-api-key": "test-key"},
        json={
            "total_stake": 1000,
            "legs": [
                {"market": "moneyline_3way", "selection": "home", "bookmaker": "A", "decimal_odds": 10.0},
                {"market": "moneyline_3way", "selection": "away", "bookmaker": "B", "decimal_odds": 10.0},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()["groups"]
    assert len(body) == 1
    assert body[0]["verified"] is False
    assert "선택지" in body[0]["warning"]
    # Still shows the raw numbers even though it can't be verified.
    assert body[0]["total_implied_probability"] == pytest.approx(0.2)


def test_scan_group_field_mixes_different_markets_and_marks_unverified(api_client):
    """A shared ``group`` string forces legs from genuinely different
    markets (moneyline + totals here) into one combined calculation --
    always unverified, with a warning about correlation, never silently
    treated as a real arbitrage."""
    resp = api_client.post(
        "/calculator/scan",
        headers={"x-api-key": "test-key"},
        json={
            "total_stake": 1000,
            "legs": [
                {"market": "moneyline_2way", "selection": "home", "bookmaker": "A", "decimal_odds": 3.0, "group": "mix1"},
                {"market": "totals", "line": 2.5, "selection": "over", "bookmaker": "B", "decimal_odds": 3.0, "group": "mix1"},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()["groups"]
    assert len(body) == 1
    assert body[0]["verified"] is False
    assert "독립이 아니" in body[0]["warning"]
    assert len(body[0]["legs"]) == 2


def test_scan_marks_correct_score_and_custom_markets_as_unverified(api_client):
    resp = api_client.post(
        "/calculator/scan",
        headers={"x-api-key": "test-key"},
        json={
            "total_stake": 1000,
            "legs": [
                {"market": "correct_score", "selection": "2-1", "bookmaker": "A", "decimal_odds": 8.0},
                {"market": "correct_score", "selection": "1-0", "bookmaker": "A", "decimal_odds": 7.0},
                {"market": "코너킥 오버 9.5", "selection": "over", "bookmaker": "B", "decimal_odds": 1.85},
                {"market": "코너킥 오버 9.5", "selection": "under", "bookmaker": "B", "decimal_odds": 1.85},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()["groups"]
    labels = {g["market"] for g in body}
    assert labels == {"correct_score", "코너킥 오버 9.5"}
    assert all(g["verified"] is False for g in body)
    assert all("경우의 수" in g["warning"] for g in body)


# ---------------------------------------------------------------------
# min_hit_rate_percent: partial-coverage picks (drop the least likely
# outcome to raise margin, at the cost of a real chance of losing it all).
# ---------------------------------------------------------------------


def test_scan_hit_rate_drops_the_draw_to_raise_margin(api_client):
    """Classic case: a 3-way market isn't a full arbitrage, but skipping
    the draw (lowest fair probability here) to hedge only home/away can
    clear a lower hit-rate target with a better margin -- and must show
    up in its own hit_rate_picks list, separate from `groups`."""
    resp = api_client.post(
        "/calculator/scan",
        headers={"x-api-key": "test-key"},
        json={
            "total_stake": 100000,
            "min_hit_rate_percent": 70,
            "legs": [
                {"market": "moneyline_3way", "selection": "home", "bookmaker": "A", "decimal_odds": 2.05},
                {"market": "moneyline_3way", "selection": "draw", "bookmaker": "B", "decimal_odds": 3.40},
                {"market": "moneyline_3way", "selection": "away", "bookmaker": "C", "decimal_odds": 4.20},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["hit_rate_picks"], "expected at least one hit-rate pick"
    pick = body["hit_rate_picks"][0]
    assert pick["market"] == "moneyline_3way"
    assert pick["achieved_hit_rate_percent"] >= 70.0 - 1e-6
    assert pick["target_hit_rate_percent"] == 70.0
    assert "draw" in pick["excluded_selections"]
    assert {leg["selection"] for leg in pick["legs"]} == {"home", "away"}

    # The full 3-way group is still reported too, in the separate list,
    # and its margin must never exceed the partial pick's (dropping an
    # outcome can only ever raise or match the margin).
    full_group = next(g for g in body["groups"] if g["market"] == "moneyline_3way")
    assert pick["margin_percent"] >= full_group["margin_percent"] - 1e-9


def test_scan_hit_rate_omitted_by_default(api_client):
    resp = api_client.post(
        "/calculator/scan",
        headers={"x-api-key": "test-key"},
        json={
            "total_stake": 1000,
            "legs": [
                {"market": "moneyline_2way", "selection": "home", "bookmaker": "A", "decimal_odds": 1.80},
                {"market": "moneyline_2way", "selection": "away", "bookmaker": "B", "decimal_odds": 1.80},
            ],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["hit_rate_picks"] == []


def test_scan_hit_rate_never_computed_for_unverified_groups(api_client):
    """correct_score has no reliable 'everything else' probability, so it
    must never produce a hit-rate pick even when a threshold is set."""
    resp = api_client.post(
        "/calculator/scan",
        headers={"x-api-key": "test-key"},
        json={
            "total_stake": 1000,
            "min_hit_rate_percent": 50,
            "legs": [
                {"market": "correct_score", "selection": "2-1", "bookmaker": "A", "decimal_odds": 8.0},
                {"market": "correct_score", "selection": "1-0", "bookmaker": "A", "decimal_odds": 7.0},
            ],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["hit_rate_picks"] == []


# ---------------------------------------------------------------------
# /calculator/system-bet: combining INDEPENDENT selections (typically
# different matches) into a System M/N bet.
# ---------------------------------------------------------------------


def test_system_bet_requires_api_key(api_client):
    resp = api_client.post(
        "/calculator/system-bet",
        json={
            "total_stake": 1000,
            "min_hit_rate_percent": 50,
            "legs": [
                {"label": "A win", "decimal_odds": 2.0},
                {"label": "B win", "decimal_odds": 2.0},
            ],
        },
    )
    assert resp.status_code in (401, 422)


def test_system_bet_builds_a_yankee_style_system(api_client):
    resp = api_client.post(
        "/calculator/system-bet",
        headers={"x-api-key": "test-key"},
        json={
            "total_stake": 11000,
            "min_hit_rate_percent": 1,  # trivially low -> picks the strictest M that still clears it
            "legs": [
                {"label": "A win", "bookmaker": "X", "decimal_odds": 2.0, "probability_percent": 60},
                {"label": "B win", "bookmaker": "Y", "decimal_odds": 2.0, "probability_percent": 60},
                {"label": "C win", "bookmaker": "Z", "decimal_odds": 2.0, "probability_percent": 60},
                {"label": "D win", "bookmaker": "W", "decimal_odds": 2.0, "probability_percent": 60},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["num_selections"] == 4
    # A near-0% target picks the strictest system that still trivially
    # clears it -- with 4 independent 60%-chance legs, that's "all 4"
    # (min_hits=4), a single bet.
    assert body["min_hits"] == 4
    assert body["num_bets"] == 1
    assert body["used_naive_probability"] is False
    assert "확정 수익이 아닙니다" in body["warning"]


def test_system_bet_naive_probability_defaults_to_implied_and_flags_it(api_client):
    resp = api_client.post(
        "/calculator/system-bet",
        headers={"x-api-key": "test-key"},
        json={
            "total_stake": 10000,
            "min_hit_rate_percent": 50,
            "legs": [
                {"label": "A", "decimal_odds": 2.0},
                {"label": "B", "decimal_odds": 2.0},
                {"label": "C", "decimal_odds": 2.0},
            ],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["used_naive_probability"] is True
    assert body["expected_profit_percent"] == pytest.approx(0.0, abs=1e-4)
    assert "배당의 역수" in body["warning"]


def test_system_bet_rejects_too_few_legs(api_client):
    resp = api_client.post(
        "/calculator/system-bet",
        headers={"x-api-key": "test-key"},
        json={
            "total_stake": 1000,
            "min_hit_rate_percent": 50,
            "legs": [{"label": "A", "decimal_odds": 2.0}],
        },
    )
    assert resp.status_code == 400


def test_system_bet_rejects_too_many_legs(api_client):
    resp = api_client.post(
        "/calculator/system-bet",
        headers={"x-api-key": "test-key"},
        json={
            "total_stake": 1000,
            "min_hit_rate_percent": 50,
            "legs": [{"label": f"L{i}", "decimal_odds": 2.0} for i in range(20)],
        },
    )
    assert resp.status_code == 400
