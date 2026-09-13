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
    # provider for the duration of this module so the match browser has
    # real (fixture) data to serve, without the app itself ever knowing
    # about dummy data. This mirrors exactly how a real deployment would
    # behave once PINNACLE_USERNAME/ODDS_API_KEY etc. are actually configured.
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


# ---------------------------------------------------------------------
# /matches: auto-fetched upcoming events + their latest odds, seeded by
# the one startup poll against the fixture provider.
# ---------------------------------------------------------------------


def test_matches_requires_api_key(api_client):
    resp = api_client.get("/matches")
    assert resp.status_code in (401, 422)


def test_matches_rejects_wrong_key(api_client):
    resp = api_client.get("/matches", headers={"x-api-key": "wrong"})
    assert resp.status_code == 401


def test_matches_lists_fixture_events_with_selections(api_client):
    resp = api_client.get("/matches", headers={"x-api-key": "test-key"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) >= 1
    arsenal = next(m for m in body if "Arsenal" in m["event"])
    assert arsenal["event"] == "Arsenal vs Chelsea"
    markets = {s["market"] for s in arsenal["selections"]}
    assert "moneyline_3way" in markets
    home_selection = next(s for s in arsenal["selections"] if s["market"] == "moneyline_3way" and s["selection"] == "home")
    assert home_selection["decimal_odds"] > 1.0
    assert home_selection["selection_label"] == "Arsenal"


def test_matches_respects_sport_filter(api_client):
    resp = api_client.get("/matches", headers={"x-api-key": "test-key"}, params={"sport": "basketball"})
    assert resp.status_code == 200
    body = resp.json()
    assert body
    assert all(m["sport"] == "basketball" for m in body)


# ---------------------------------------------------------------------
# /hedge-box/calculate: back-solve stakes for a fixed profit target.
# ---------------------------------------------------------------------


def test_calculate_requires_api_key(api_client):
    resp = api_client.post(
        "/hedge-box/calculate",
        json={
            "leg_a": {"label": "무승부", "decimal_odds": 3.75},
            "leg_b": {"label": "홈팀 -0.5", "decimal_odds": 1.909},
            "target_profit": 10000,
        },
    )
    assert resp.status_code in (401, 422)


def test_calculate_hits_target_profit_on_either_leg(api_client):
    resp = api_client.post(
        "/hedge-box/calculate",
        headers={"x-api-key": "test-key"},
        json={
            "leg_a": {"label": "무승부", "decimal_odds": 3.75},
            "leg_b": {"label": "홈팀 -0.5", "decimal_odds": 1.909},
            "target_profit": 10000,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["leg_a_stake"] > 0
    assert body["leg_b_stake"] > 0
    assert body["guaranteed_profit"] > 0
    assert body["guaranteed_profit"] == pytest.approx(10000, rel=0.05)
    assert body["implied_hit_rate_percent"] is None


def test_calculate_estimates_hit_rate_when_excluded_odds_given(api_client):
    resp = api_client.post(
        "/hedge-box/calculate",
        headers={"x-api-key": "test-key"},
        json={
            "leg_a": {"label": "무승부", "decimal_odds": 3.75},
            "leg_b": {"label": "홈팀 -0.5", "decimal_odds": 1.909},
            "target_profit": 10000,
            "excluded_odds": 6.0,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["implied_hit_rate_percent"] is not None
    assert 0 < body["implied_hit_rate_percent"] < 100


def test_calculate_rejects_odds_combo_with_no_margin(api_client):
    resp = api_client.post(
        "/hedge-box/calculate",
        headers={"x-api-key": "test-key"},
        json={
            "leg_a": {"label": "A", "decimal_odds": 1.5},
            "leg_b": {"label": "B", "decimal_odds": 1.5},
            "target_profit": 10000,
        },
    )
    assert resp.status_code == 400


def test_calculate_rejects_non_positive_target_profit(api_client):
    resp = api_client.post(
        "/hedge-box/calculate",
        headers={"x-api-key": "test-key"},
        json={
            "leg_a": {"label": "A", "decimal_odds": 2.0},
            "leg_b": {"label": "B", "decimal_odds": 3.0},
            "target_profit": 0,
        },
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------
# /hedge-box/send + /hedge-box/{id}: send to Telegram (no-op without
# credentials configured) and log/expose the box for its detail link.
# ---------------------------------------------------------------------


def test_send_requires_api_key(api_client):
    resp = api_client.post(
        "/hedge-box/send",
        json={
            "event": "Crystal Palace vs Ipswich",
            "league": "Premier League",
            "commence_time": "2026-09-12T23:00:00+09:00",
            "leg_a": {"label": "무승부", "decimal_odds": 3.75},
            "leg_b": {"label": "크리스탈 팰리스 -0.5", "decimal_odds": 1.909},
            "target_profit": 10000,
        },
    )
    assert resp.status_code in (401, 422)


def test_send_logs_box_and_reports_not_sent_when_telegram_unconfigured(api_client, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    from app.config import get_settings

    get_settings.cache_clear()

    resp = api_client.post(
        "/hedge-box/send",
        headers={"x-api-key": "test-key"},
        json={
            "event": "Crystal Palace vs Ipswich",
            "league": "Premier League",
            "commence_time": "2026-09-12T23:00:00+09:00",
            "leg_a": {"label": "무승부", "decimal_odds": 3.75},
            "leg_b": {"label": "크리스탈 팰리스 -0.5", "decimal_odds": 1.909},
            "target_profit": 10000,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["sent"] is False
    assert body["box_id"] > 0
    assert f"/box/{body['box_id']}" in body["detail_url"]

    detail = api_client.get(f"/hedge-box/{body['box_id']}")
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["event"] == "Crystal Palace vs Ipswich"
    assert detail_body["leg_a_label"] == "무승부"
    assert detail_body["guaranteed_profit"] == pytest.approx(10000, rel=0.05)

    get_settings.cache_clear()


def test_get_box_detail_is_public_no_api_key_needed(api_client):
    send_resp = api_client.post(
        "/hedge-box/send",
        headers={"x-api-key": "test-key"},
        json={
            "event": "West Brom vs QPR",
            "league": "EFL Championship",
            "commence_time": "2026-09-12T20:30:00+09:00",
            "leg_a": {"label": "무승부", "decimal_odds": 3.55},
            "leg_b": {"label": "웨스트 브로미치 -0.25", "decimal_odds": 1.96},
            "target_profit": 10000,
        },
    )
    box_id = send_resp.json()["box_id"]

    # No x-api-key header at all -- must still work for Telegram viewers.
    resp = api_client.get(f"/hedge-box/{box_id}")
    assert resp.status_code == 200


def test_get_box_detail_404_for_unknown_id(api_client):
    resp = api_client.get("/hedge-box/999999")
    assert resp.status_code == 404


def test_send_rejects_odds_combo_with_no_margin(api_client):
    resp = api_client.post(
        "/hedge-box/send",
        headers={"x-api-key": "test-key"},
        json={
            "event": "A vs B",
            "league": "",
            "commence_time": "2026-09-12T20:30:00+09:00",
            "leg_a": {"label": "A", "decimal_odds": 1.5},
            "leg_b": {"label": "B", "decimal_odds": 1.5},
            "target_profit": 10000,
        },
    )
    assert resp.status_code == 400
