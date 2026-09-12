import httpx
import pytest

from app.core.enums import MarketType, Sport
from app.engine.arbitrage import find_arbitrage
from app.providers.base import parse_decimal_odds, parse_float
from app.providers.oddsapi import OddsApiProvider, parse_oddsapi_response
from app.providers.pinnacle import PinnacleProvider, parse_fixtures, parse_pinnacle_odds
from tests.fixtures import demo_quotes


def test_fixture_quotes_produce_a_real_moneyline_arbitrage():
    quotes = demo_quotes([Sport.SOCCER, Sport.BASKETBALL])
    arsenal_ml = [
        q
        for q in quotes
        if q.market == MarketType.MONEYLINE_3WAY and q.event.home_team == "Arsenal"
    ]
    result = find_arbitrage(arsenal_ml)
    assert result is not None
    assert result.is_arbitrage
    assert result.margin_percent > 0


def test_fixture_quotes_two_way_moneyline_arbitrage():
    quotes = demo_quotes([Sport.BASKETBALL])
    ml = [q for q in quotes if q.market == MarketType.MONEYLINE_2WAY]
    result = find_arbitrage(ml)
    assert result is not None
    assert result.is_arbitrage


FIXTURES_PAYLOAD = {
    "sportId": 29,
    "last": 12345,
    "league": [
        {
            "id": 1,
            "name": "England - Premier League",
            "events": [
                {"id": 100, "home": "Arsenal", "away": "Chelsea", "starts": "2026-02-01T15:00:00Z"},
            ],
        }
    ],
}

ODDS_PAYLOAD = {
    "sportId": 29,
    "last": 12345,
    "leagues": [
        {
            "id": 1,
            "events": [
                {
                    "id": 100,
                    "periods": [
                        {
                            "number": 0,
                            "moneyline": {"home": 2.10, "draw": 3.40, "away": 4.00},
                            "spreads": [{"hdp": -0.5, "home": 1.90, "away": 1.95}],
                            "totals": [{"points": 2.5, "over": 1.95, "under": 1.95}],
                        },
                        {"number": 1, "moneyline": {"home": 1.50, "away": 2.50}},
                    ],
                }
            ],
        }
    ],
}


def test_parse_fixtures_handles_documented_shape():
    fixtures = parse_fixtures(FIXTURES_PAYLOAD)
    assert 100 in fixtures
    info = fixtures[100]
    assert info.home == "Arsenal"
    assert info.away == "Chelsea"
    assert info.league_name == "England - Premier League"


def test_parse_fixtures_uses_singular_league_key_not_leagues():
    # The fixtures endpoint's top-level key is "league" (singular), unlike
    # the odds endpoint's "leagues" -- a payload using the wrong key must
    # parse to nothing rather than silently succeeding.
    assert parse_fixtures({"leagues": [{"id": 1, "name": "X", "events": []}]}) == {}


def test_pinnacle_parser_handles_documented_shape():
    fixtures = parse_fixtures(FIXTURES_PAYLOAD)
    quotes = parse_pinnacle_odds(ODDS_PAYLOAD, Sport.SOCCER, fixtures)
    markets = {(q.market, q.selection, q.line) for q in quotes}
    assert (MarketType.MONEYLINE_3WAY, "home", None) in markets
    assert (MarketType.ASIAN_HANDICAP, "away", -0.5) in markets
    assert (MarketType.TOTALS, "over", 2.5) in markets
    assert all(q.event.home_team == "Arsenal" and q.event.away_team == "Chelsea" for q in quotes)
    # period 1 (first half) must be ignored, only full-match period 0 parsed
    assert len(quotes) == 7


def test_pinnacle_parser_skips_odds_for_events_missing_from_fixtures():
    # The odds endpoint carries no team names/start times at all -- an
    # event id with no matching fixture can't be normalized and must be
    # dropped rather than guessed at.
    quotes = parse_pinnacle_odds(ODDS_PAYLOAD, Sport.SOCCER, fixtures={})
    assert quotes == []


def test_pinnacle_parser_skips_malformed_records_without_raising():
    payload = {
        "leagues": [
            {
                "id": 1,
                "events": [
                    {"id": 100, "periods": [{"number": 0, "spreads": [{"hdp": "oops"}]}]},
                    {"id": "not-an-int"},
                ],
            }
        ]
    }
    quotes = parse_pinnacle_odds(payload, Sport.SOCCER, parse_fixtures(FIXTURES_PAYLOAD))
    assert quotes == []


def test_oddsapi_parser_normalizes_h2h_totals_and_spreads():
    payload = [
        {
            "sport_title": "EPL",
            "commence_time": "2026-02-01T15:00:00Z",
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "bookmakers": [
                {
                    "title": "SomeBook",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Arsenal", "price": 2.10},
                                {"name": "Draw", "price": 3.40},
                                {"name": "Chelsea", "price": 4.00},
                            ],
                        },
                        {
                            "key": "totals",
                            "outcomes": [
                                {"name": "Over", "price": 1.95, "point": 2.5},
                                {"name": "Under", "price": 1.95, "point": 2.5},
                            ],
                        },
                        {
                            "key": "spreads",
                            "outcomes": [
                                {"name": "Arsenal", "price": 1.90, "point": -0.5},
                                {"name": "Chelsea", "price": 1.95, "point": 0.5},
                            ],
                        },
                    ],
                }
            ],
        }
    ]
    quotes = parse_oddsapi_response(payload, Sport.SOCCER)
    by_market = {(q.market, q.selection, q.line): q.decimal_odds for q in quotes}
    assert by_market[(MarketType.MONEYLINE_3WAY, "home", None)] == 2.10
    assert by_market[(MarketType.MONEYLINE_3WAY, "draw", None)] == 3.40
    assert by_market[(MarketType.TOTALS, "over", 2.5)] == 1.95
    assert by_market[(MarketType.ASIAN_HANDICAP, "home", -0.5)] == 1.90
    assert by_market[(MarketType.ASIAN_HANDICAP, "away", -0.5)] == 1.95


def test_oddsapi_parser_normalizes_btts_and_correct_score():
    payload = [
        {
            "sport_title": "EPL",
            "commence_time": "2026-02-01T15:00:00Z",
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "bookmakers": [
                {
                    "title": "SomeBook",
                    "markets": [
                        {
                            "key": "btts",
                            "outcomes": [
                                {"name": "Yes", "price": 1.80},
                                {"name": "No", "price": 2.00},
                            ],
                        },
                        {
                            "key": "correct_score",
                            "outcomes": [
                                {"name": "2:1", "price": 13.0},
                                {"name": "1-0", "price": 7.0},
                                {"name": "Any Other Home Win", "price": 5.0},  # no digits, must be skipped
                            ],
                        },
                    ],
                }
            ],
        }
    ]
    quotes = parse_oddsapi_response(payload, Sport.SOCCER)
    by_market = {(q.market, q.selection): q.decimal_odds for q in quotes}
    assert by_market[(MarketType.BOTH_TEAMS_TO_SCORE, "yes")] == 1.80
    assert by_market[(MarketType.BOTH_TEAMS_TO_SCORE, "no")] == 2.00
    assert by_market[(MarketType.CORRECT_SCORE, "2-1")] == 13.0
    assert by_market[(MarketType.CORRECT_SCORE, "1-0")] == 7.0
    assert len(quotes) == 4  # the digit-less catch-all bucket was skipped


def _oddsapi_transport(core_status: int = 200, exotic_status: int = 200, calls: list | None = None):
    core_payload = [
        {
            "sport_title": "EPL",
            "commence_time": "2026-02-01T15:00:00Z",
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "bookmakers": [
                {"title": "SomeBook", "markets": [{"key": "h2h", "outcomes": [{"name": "Arsenal", "price": 2.10}, {"name": "Chelsea", "price": 3.50}]}]}
            ],
        }
    ]
    exotic_payload = [
        {
            "sport_title": "EPL",
            "commence_time": "2026-02-01T15:00:00Z",
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "bookmakers": [
                {"title": "SomeBook", "markets": [{"key": "btts", "outcomes": [{"name": "Yes", "price": 1.80}]}]}
            ],
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        markets = request.url.params.get("markets")
        if calls is not None:
            calls.append(markets)
        if markets == "h2h,spreads,totals":
            if core_status != 200:
                return httpx.Response(core_status, json={"message": "error"})
            return httpx.Response(200, json=core_payload)
        if markets == "btts,correct_score":
            if exotic_status != 200:
                return httpx.Response(exotic_status, json={"message": "error"})
            return httpx.Response(200, json=exotic_payload)
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_oddsapi_provider_fetches_core_and_exotic_markets_separately():
    calls: list[str] = []
    provider = OddsApiProvider(
        "https://api.the-odds-api.com", "key", ["soccer_epl"], transport=_oddsapi_transport(calls=calls)
    )
    quotes = await provider.fetch([Sport.SOCCER])
    assert set(calls) == {"h2h,spreads,totals", "btts,correct_score"}
    markets = {q.market for q in quotes}
    assert MarketType.MONEYLINE_2WAY in markets
    assert MarketType.BOTH_TEAMS_TO_SCORE in markets


@pytest.mark.asyncio
async def test_oddsapi_provider_exotic_markets_failure_never_costs_core_markets():
    """A plan that doesn't include additional markets (btts/correct_score)
    would 4xx on that request -- the core h2h/spreads/totals quotes the
    arbitrage engine depends on must still come through.
    """
    provider = OddsApiProvider(
        "https://api.the-odds-api.com", "key", ["soccer_epl"], transport=_oddsapi_transport(exotic_status=422)
    )
    quotes = await provider.fetch([Sport.SOCCER])
    assert any(q.market == MarketType.MONEYLINE_2WAY for q in quotes)
    assert not any(q.market == MarketType.BOTH_TEAMS_TO_SCORE for q in quotes)


def test_parse_float_rejects_non_numeric_and_bool():
    assert parse_float("2.5") == 2.5
    assert parse_float(2.5) == 2.5
    assert parse_float("oops") is None
    assert parse_float(None) is None
    assert parse_float({"nested": "dict"}) is None
    assert parse_float(True) is None  # bool is an int subclass, never a real value here


def test_parse_decimal_odds_rejects_odds_at_or_below_one():
    assert parse_decimal_odds(2.10) == 2.10
    assert parse_decimal_odds("1.95") == 1.95
    assert parse_decimal_odds(1.0) is None
    assert parse_decimal_odds(0.0) is None
    assert parse_decimal_odds(-2.10) is None
    assert parse_decimal_odds("garbage") is None


def test_pinnacle_parser_top_level_shape_guards():
    fixtures = parse_fixtures(FIXTURES_PAYLOAD)
    assert parse_pinnacle_odds({"leagues": "not-a-list"}, Sport.SOCCER, fixtures) == []
    assert parse_pinnacle_odds({"leagues": [{"events": "not-a-list"}]}, Sport.SOCCER, fixtures) == []
    assert parse_pinnacle_odds("not-even-a-dict", Sport.SOCCER, fixtures) == []
    assert parse_pinnacle_odds(None, Sport.SOCCER, fixtures) == []
    assert parse_pinnacle_odds([1, 2, 3], Sport.SOCCER, fixtures) == []
    assert parse_fixtures("not-a-dict") == {}
    assert parse_fixtures({"league": "not-a-list"}) == {}


def test_pinnacle_parser_isolates_one_malformed_event_from_the_rest():
    """The real bug this guards against: one event with a completely
    wrong-shaped ``periods`` (a dict instead of a list) used to raise
    partway through parsing and abort every OTHER event in the same
    payload too. Now it must only drop the bad event.
    """
    fixtures = {
        **parse_fixtures(FIXTURES_PAYLOAD),
        **parse_fixtures(
            {
                "league": [
                    {"id": 1, "name": "L", "events": [{"id": 200, "home": "Bad", "away": "Event", "starts": "2026-02-01T15:00:00Z"}]}
                ]
            }
        ),
    }
    payload = {
        "leagues": [
            {
                "id": 1,
                "events": [
                    {"id": 200, "periods": {"not": "a list"}},
                    {"id": 100, "periods": [{"number": 0, "moneyline": {"home": 2.10, "away": 3.50}}]},
                ],
            }
        ]
    }
    quotes = parse_pinnacle_odds(payload, Sport.SOCCER, fixtures)
    assert len(quotes) == 2
    assert all(q.event.home_team == "Arsenal" for q in quotes)


def test_pinnacle_parser_rejects_non_positive_odds():
    fixtures = parse_fixtures(FIXTURES_PAYLOAD)
    payload = {
        "leagues": [
            {"id": 1, "events": [{"id": 100, "periods": [{"number": 0, "moneyline": {"home": 1.0, "away": 0}}]}]}
        ]
    }
    assert parse_pinnacle_odds(payload, Sport.SOCCER, fixtures) == []


def _pinnacle_transport(sports_payload=None, fixtures_payload=None, odds_payload=None, calls: list | None = None):
    sports_payload = sports_payload if sports_payload is not None else {"sports": [{"id": 29, "name": "Soccer"}]}
    fixtures_payload = fixtures_payload if fixtures_payload is not None else FIXTURES_PAYLOAD
    odds_payload = odds_payload if odds_payload is not None else ODDS_PAYLOAD

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request.url.path)
        if request.url.path == "/v2/sports":
            return httpx.Response(200, json=sports_payload)
        if request.url.path == "/v1/fixtures":
            return httpx.Response(200, json=fixtures_payload)
        if request.url.path == "/v2/odds":
            return httpx.Response(200, json=odds_payload)
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_pinnacle_provider_fetches_and_joins_fixtures_with_odds():
    calls: list[str] = []
    provider = PinnacleProvider("https://api.pinnacle.com", "user", "pass", transport=_pinnacle_transport(calls=calls))
    quotes = await provider.fetch([Sport.SOCCER])
    assert len(quotes) == 7
    assert all(q.event.home_team == "Arsenal" for q in quotes)
    assert calls == ["/v2/sports", "/v1/fixtures", "/v2/odds"]


@pytest.mark.asyncio
async def test_pinnacle_provider_throttles_odds_requests_per_fair_use_policy():
    """Pinnacle's documented fair-use limit is 1 request / 2 minutes per
    sportId for /odds (and /fixtures). Calling fetch() twice in a row must
    reuse the cached result instead of hitting the API again.
    """
    calls: list[str] = []
    provider = PinnacleProvider("https://api.pinnacle.com", "user", "pass", transport=_pinnacle_transport(calls=calls))

    first = await provider.fetch([Sport.SOCCER])
    second = await provider.fetch([Sport.SOCCER])

    assert len(first) == 7
    assert second == first  # reused from cache, not re-fetched
    # /v2/sports is cached after the first call too (once-ever), so only
    # /v1/fixtures and /v2/odds from the FIRST cycle should appear at all.
    assert calls == ["/v2/sports", "/v1/fixtures", "/v2/odds"]


@pytest.mark.asyncio
async def test_pinnacle_provider_returns_empty_when_not_configured():
    provider = PinnacleProvider("https://api.pinnacle.com", "", "", transport=_pinnacle_transport())
    assert await provider.fetch([Sport.SOCCER]) == []


def test_oddsapi_parser_top_level_shape_guards():
    assert parse_oddsapi_response({"message": "error from upstream"}, Sport.SOCCER) == []
    assert parse_oddsapi_response(None, Sport.SOCCER) == []
    assert parse_oddsapi_response("nope", Sport.SOCCER) == []


def test_oddsapi_parser_isolates_one_malformed_event_from_the_rest():
    payload = [
        {
            "commence_time": "2026-02-01T15:00:00Z",
            "home_team": "Bad",
            "away_team": "Event",
            "bookmakers": "not-a-list",  # completely wrong shape
        },
        {
            "commence_time": "2026-02-01T15:00:00Z",
            "home_team": "Good",
            "away_team": "Event",
            "bookmakers": [
                {
                    "title": "SomeBook",
                    "markets": [
                        {"key": "h2h", "outcomes": [{"name": "Good", "price": 2.10}, {"name": "Event", "price": 3.50}]}
                    ],
                }
            ],
        },
    ]
    quotes = parse_oddsapi_response(payload, Sport.SOCCER)
    assert len(quotes) == 2
    assert all(q.event.home_team == "Good" for q in quotes)


def test_oddsapi_parser_rejects_non_positive_odds():
    payload = [
        {
            "commence_time": "2026-02-01T15:00:00Z",
            "home_team": "A",
            "away_team": "B",
            "bookmakers": [
                {
                    "title": "SomeBook",
                    "markets": [{"key": "h2h", "outcomes": [{"name": "A", "price": 1.0}, {"name": "B", "price": -3.5}]}],
                }
            ],
        }
    ]
    assert parse_oddsapi_response(payload, Sport.SOCCER) == []


def test_oddsapi_parser_drops_inconsistent_spread_points():
    payload = [
        {
            "commence_time": "2026-02-01T15:00:00Z",
            "home_team": "A",
            "away_team": "B",
            "bookmakers": [
                {
                    "title": "BadBook",
                    "markets": [
                        {
                            "key": "spreads",
                            "outcomes": [
                                {"name": "A", "price": 1.9, "point": -1.5},
                                {"name": "B", "price": 1.9, "point": 2.0},  # inconsistent, should be +1.5
                            ],
                        }
                    ],
                }
            ],
        }
    ]
    quotes = parse_oddsapi_response(payload, Sport.SOCCER)
    assert quotes == []
