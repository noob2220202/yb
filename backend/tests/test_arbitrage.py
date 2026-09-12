from datetime import datetime, timezone

import pytest

from app.core.enums import MarketType, Sport
from app.core.schemas import NormalizedEvent, OddsQuote
from app.engine.arbitrage import (
    allocate_stakes,
    find_arbitrage,
    is_supported_line,
    push_possible,
)

EVENT = NormalizedEvent(
    sport=Sport.SOCCER,
    home_team="Arsenal",
    away_team="Chelsea",
    commence_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
    league="EPL",
)


def quote(bookmaker: str, market: MarketType, selection: str, odds: float, line: float | None = None) -> OddsQuote:
    return OddsQuote(event=EVENT, bookmaker=bookmaker, market=market, selection=selection, decimal_odds=odds, line=line)


def test_three_way_arbitrage_found_across_books():
    quotes = [
        quote("BookA", MarketType.MONEYLINE_3WAY, "home", 2.10),
        quote("BookB", MarketType.MONEYLINE_3WAY, "draw", 3.60),
        quote("BookC", MarketType.MONEYLINE_3WAY, "away", 4.20),
    ]
    result = find_arbitrage(quotes)
    assert result is not None
    assert result.is_arbitrage
    assert result.total_implied_probability < 1.0
    assert result.margin_percent > 0

    plan = allocate_stakes(result, total_stake=1000)
    assert plan.guaranteed_profit > 0
    total_staked = sum(leg.stake for leg in plan.legs)
    assert total_staked == pytest.approx(1000, abs=0.5)

    # Every outcome pays out (approximately) the same net amount.
    payouts = [leg.payout for leg in plan.legs]
    assert max(payouts) - min(payouts) < 0.5
    for payout in payouts:
        assert payout - 1000 == pytest.approx(plan.guaranteed_profit, abs=0.5)


def test_uses_best_odds_across_multiple_books_per_selection():
    quotes = [
        quote("BookA", MarketType.MONEYLINE_2WAY, "home", 1.80),
        quote("BookB", MarketType.MONEYLINE_2WAY, "home", 1.95),  # better, should win
        quote("BookC", MarketType.MONEYLINE_2WAY, "away", 2.10),
    ]
    result = find_arbitrage(quotes)
    assert result is not None
    home_leg = next(leg for leg in result.legs if leg.selection == "home")
    assert home_leg.decimal_odds == 1.95
    assert home_leg.bookmaker == "BookB"


def test_no_arbitrage_when_margin_is_negative():
    quotes = [
        quote("BookA", MarketType.MONEYLINE_2WAY, "home", 1.80),
        quote("BookB", MarketType.MONEYLINE_2WAY, "away", 1.90),
    ]
    result = find_arbitrage(quotes)
    assert result is not None
    assert not result.is_arbitrage
    with pytest.raises(ValueError):
        allocate_stakes(result, total_stake=100)


def test_incomplete_partition_returns_none():
    quotes = [quote("BookA", MarketType.MONEYLINE_3WAY, "home", 10.0)]
    assert find_arbitrage(quotes) is None


def test_empty_quotes_returns_none():
    assert find_arbitrage([]) is None


def test_mismatched_market_or_line_raises():
    quotes = [
        quote("BookA", MarketType.TOTALS, "over", 2.05, line=2.5),
        quote("BookB", MarketType.TOTALS, "under", 2.05, line=3.0),
    ]
    with pytest.raises(ValueError):
        find_arbitrage(quotes)


@pytest.mark.parametrize(
    "line,expected_supported,expected_push",
    [
        (2.5, True, False),
        (2.0, True, True),
        (2.25, False, False),
        (2.75, False, False),
        (-1.5, True, False),
        (-1.0, True, True),
    ],
)
def test_line_support_and_push_classification(line, expected_supported, expected_push):
    assert is_supported_line(MarketType.TOTALS, line) is expected_supported
    assert push_possible(MarketType.TOTALS, line) is expected_push


def test_quarter_line_totals_excluded_from_arbitrage():
    quotes = [
        quote("BookA", MarketType.TOTALS, "over", 5.0, line=2.25),
        quote("BookB", MarketType.TOTALS, "under", 5.0, line=2.25),
    ]
    assert find_arbitrage(quotes) is None


def test_whole_number_totals_line_flagged_as_push_possible():
    quotes = [
        quote("BookA", MarketType.TOTALS, "over", 2.10, line=2.0),
        quote("BookB", MarketType.TOTALS, "under", 2.10, line=2.0),
    ]
    result = find_arbitrage(quotes)
    assert result is not None
    assert result.push_possible is True
    assert result.is_arbitrage


def test_half_line_totals_never_pushes():
    quotes = [
        quote("BookA", MarketType.TOTALS, "over", 2.10, line=2.5),
        quote("BookB", MarketType.TOTALS, "under", 2.10, line=2.5),
    ]
    result = find_arbitrage(quotes)
    assert result is not None
    assert result.push_possible is False
