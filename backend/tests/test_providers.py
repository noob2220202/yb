import asyncio

from app.core.enums import MarketType, Sport
from app.engine.arbitrage import find_arbitrage
from app.providers.base import parse_decimal_odds, parse_float
from app.providers.demo import DemoProvider
from app.providers.oddsapi import parse_oddsapi_response
from app.providers.pinnacle import parse_pinnacle_odds


def test_demo_provider_produces_a_real_moneyline_arbitrage():
    quotes = asyncio.run(DemoProvider().fetch([Sport.SOCCER, Sport.BASKETBALL]))
    arsenal_ml = [
        q
        for q in quotes
        if q.market == MarketType.MONEYLINE_3WAY and q.event.home_team == "Arsenal"
    ]
    result = find_arbitrage(arsenal_ml)
    assert result is not None
    assert result.is_arbitrage
    assert result.margin_percent > 0


def test_demo_provider_two_way_moneyline_arbitrage():
    quotes = asyncio.run(DemoProvider().fetch([Sport.BASKETBALL]))
    ml = [q for q in quotes if q.market == MarketType.MONEYLINE_2WAY]
    result = find_arbitrage(ml)
    assert result is not None
    assert result.is_arbitrage


def test_pinnacle_parser_handles_documented_shape():
    payload = {
        "leagues": [
            {
                "name": "England - Premier League",
                "events": [
                    {
                        "id": 1,
                        "home": "Arsenal",
                        "away": "Chelsea",
                        "starts": "2026-02-01T15:00:00Z",
                        "periods": [
                            {
                                "number": 0,
                                "money_line": {"home": 2.10, "draw": 3.40, "away": 4.00},
                                "spreads": [{"hdp": -0.5, "home": 1.90, "away": 1.95}],
                                "totals": [{"points": 2.5, "over": 1.95, "under": 1.95}],
                            },
                            {"number": 1, "money_line": {"home": 1.50, "away": 2.50}},
                        ],
                    }
                ],
            }
        ]
    }
    quotes = parse_pinnacle_odds(payload, Sport.SOCCER)
    markets = {(q.market, q.selection, q.line) for q in quotes}
    assert (MarketType.MONEYLINE_3WAY, "home", None) in markets
    assert (MarketType.ASIAN_HANDICAP, "away", -0.5) in markets
    assert (MarketType.TOTALS, "over", 2.5) in markets
    # period 1 (first half) must be ignored, only full-match period 0 parsed
    assert len(quotes) == 7


def test_pinnacle_parser_skips_malformed_records_without_raising():
    payload = {
        "leagues": [
            {
                "name": "L",
                "events": [
                    {"home": "A", "away": "B", "starts": "not-a-date", "periods": [{"number": 0, "spreads": [{"hdp": "oops"}]}]},
                    {"away": "missing home field"},
                ],
            }
        ]
    }
    quotes = parse_pinnacle_odds(payload, Sport.SOCCER)
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
    assert parse_pinnacle_odds({"leagues": "not-a-list"}, Sport.SOCCER) == []
    assert parse_pinnacle_odds({"leagues": [{"events": "not-a-list"}]}, Sport.SOCCER) == []
    assert parse_pinnacle_odds("not-even-a-dict", Sport.SOCCER) == []
    assert parse_pinnacle_odds(None, Sport.SOCCER) == []
    assert parse_pinnacle_odds([1, 2, 3], Sport.SOCCER) == []


def test_pinnacle_parser_isolates_one_malformed_event_from_the_rest():
    """The real bug this guards against: one event with a completely
    wrong-shaped ``periods`` (a dict instead of a list) used to raise
    partway through parsing and abort every OTHER event in the same
    payload too. Now it must only drop the bad event.
    """
    payload = {
        "leagues": [
            {
                "name": "L",
                "events": [
                    {
                        "home": "Bad",
                        "away": "Event",
                        "starts": "2026-02-01T15:00:00Z",
                        "periods": {"not": "a list"},
                    },
                    {
                        "home": "Good",
                        "away": "Event",
                        "starts": "2026-02-01T15:00:00Z",
                        "periods": [
                            {"number": 0, "money_line": {"home": 2.10, "away": 3.50}}
                        ],
                    },
                ],
            }
        ]
    }
    quotes = parse_pinnacle_odds(payload, Sport.SOCCER)
    assert len(quotes) == 2
    assert all(q.event.home_team == "Good" for q in quotes)


def test_pinnacle_parser_rejects_non_positive_odds():
    payload = {
        "leagues": [
            {
                "name": "L",
                "events": [
                    {
                        "home": "A",
                        "away": "B",
                        "starts": "2026-02-01T15:00:00Z",
                        "periods": [
                            {"number": 0, "money_line": {"home": 1.0, "away": 0}},
                        ],
                    }
                ],
            }
        ]
    }
    assert parse_pinnacle_odds(payload, Sport.SOCCER) == []


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
