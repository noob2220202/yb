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
        (2.25, True, False),
        (2.75, True, False),
        (-1.5, True, False),
        (-1.0, True, True),
    ],
)
def test_line_support_and_push_classification(line, expected_supported, expected_push):
    assert is_supported_line(MarketType.TOTALS, line) is expected_supported
    assert push_possible(MarketType.TOTALS, line) is expected_push


# ---------------------------------------------------------------------
# Quarter lines (.25/.75) — see app/engine/arbitrage.py module docstring
# for the 3-bucket (decisive win / marginal half win-or-loss / decisive
# loss) settlement model this exercises.
# ---------------------------------------------------------------------


def test_quarter_line_totals_generous_odds_found_as_arbitrage():
    quotes = [
        quote("BookA", MarketType.TOTALS, "over", 5.0, line=2.25),
        quote("BookB", MarketType.TOTALS, "under", 5.0, line=2.25),
    ]
    result = find_arbitrage(quotes)
    assert result is not None
    assert result.is_arbitrage
    assert result.margin_percent > 0
    assert result.push_possible is False
    assert result.stake_fractions is not None
    assert sum(result.stake_fractions.values()) == pytest.approx(1.0)


def test_quarter_line_single_book_normal_vig_never_an_arbitrage():
    """A single book's own two sides of a quarter line, priced with
    ordinary overround, must never look like an arbitrage -- same
    invariant as the plain-partition markets."""
    quotes = [
        quote("Pinnacle", MarketType.ASIAN_HANDICAP, "home", 1.90, line=-0.25),
        quote("Pinnacle", MarketType.ASIAN_HANDICAP, "away", 1.95, line=-0.25),
    ]
    result = find_arbitrage(quotes)
    assert result is None or not result.is_arbitrage


def test_quarter_line_incomplete_partition_returns_none():
    quotes = [quote("BookA", MarketType.TOTALS, "over", 5.0, line=2.25)]
    assert find_arbitrage(quotes) is None


def test_quarter_line_stake_plan_matches_worst_case_across_both_marginal_flavors():
    """Cross-checks allocate_stakes' quarter-line stake split against an
    independent brute-force simulation over every plausible match margin,
    for both marginal flavors (.25 and .75) -- this is what proves the
    solved-for split (not the naive 1/odds-proportional one) actually
    achieves the worst-case profit ``margin_percent`` claims.
    """

    def settle_home(line: float, margin: int) -> tuple[float, float]:
        c_lo, c_hi = line - 0.25, line + 0.25

        def outcome(c: float) -> str:
            v = margin + c
            return "win" if v > 0 else ("lose" if v < 0 else "push")

        results = [outcome(c_lo), outcome(c_hi)]
        return (sum(0.5 for r in results if r == "win"), sum(0.5 for r in results if r == "push"))

    def settle_away(line: float, margin: int) -> tuple[float, float]:
        return settle_home(-line, -margin)

    for line, odds_home, odds_away in [(-0.25, 2.05, 2.00), (0.75, 1.98, 2.15)]:
        quotes = [
            quote("BookA", MarketType.ASIAN_HANDICAP, "home", odds_home, line=line),
            quote("BookB", MarketType.ASIAN_HANDICAP, "away", odds_away, line=line),
        ]
        result = find_arbitrage(quotes)
        assert result is not None
        plan = allocate_stakes(result, total_stake=1000.0)
        stake_by_selection = {leg.selection: leg.stake for leg in plan.legs}

        worst = min(
            stake_by_selection["home"] * (w_h * odds_home + p_h)
            + stake_by_selection["away"] * (w_a * odds_away + p_a)
            for margin in range(-8, 9)
            for (w_h, p_h) in [settle_home(line, margin)]
            for (w_a, p_a) in [settle_away(line, margin)]
        )
        assert worst - 1000.0 == pytest.approx(plan.guaranteed_profit, abs=0.5)


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
