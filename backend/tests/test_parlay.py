from datetime import datetime, timezone

import pytest

from app.core.enums import MarketType, Sport
from app.core.schemas import NormalizedEvent, OddsQuote
from app.engine.parlay import (
    MIN_LEG_FAIR_PROBABILITY,
    build_parlay_candidates,
    devig_market,
    find_parlay_value,
)


def _event(name: str) -> NormalizedEvent:
    return NormalizedEvent(
        sport=Sport.SOCCER,
        home_team=name,
        away_team="Away",
        commence_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        league="Test",
    )


def test_devig_market_sums_to_one_and_is_proportional():
    fair = devig_market({"home": 2.0, "away": 2.0})
    assert fair["home"] == pytest.approx(0.5)
    assert sum(fair.values()) == pytest.approx(1.0)

    fair2 = devig_market({"home": 1.5, "draw": 4.0, "away": 6.0})
    assert sum(fair2.values()) == pytest.approx(1.0)
    assert fair2["home"] > fair2["away"]  # shorter price -> higher fair probability


def test_build_parlay_candidates_uses_best_of_all_books_for_fair_probability():
    e = _event("A")
    quotes = [
        OddsQuote(e, "Sharp", MarketType.MONEYLINE_2WAY, "home", 1.91),
        OddsQuote(e, "Sharp", MarketType.MONEYLINE_2WAY, "away", 2.01),
        # Soft book prices "home" much more generously than Sharp does.
        OddsQuote(e, "Soft", MarketType.MONEYLINE_2WAY, "home", 2.50),
        OddsQuote(e, "Soft", MarketType.MONEYLINE_2WAY, "away", 1.60),
    ]
    candidates = build_parlay_candidates(quotes)
    by_book = {(c.bookmaker, c.selection): c for c in candidates}

    # Fair probability must be identical for both books' "home" candidate
    # (it's a property of the market's best-of price, not of either book).
    assert by_book[("Sharp", "home")].fair_probability == by_book[("Soft", "home")].fair_probability
    # Best-of "home" is Soft's 2.50 (higher than Sharp's 1.91), so fair prob
    # comes from devigging {home: 2.50, away: 2.01} (Sharp's away is best).
    expected_fair = devig_market({"home": 2.50, "away": 2.01})
    assert by_book[("Soft", "home")].fair_probability == pytest.approx(expected_fair["home"])


def test_build_parlay_candidates_skips_incomplete_partitions_and_unsupported_lines():
    e = _event("A")
    quotes = [
        OddsQuote(e, "BookA", MarketType.MONEYLINE_3WAY, "home", 2.0),  # missing draw/away
        OddsQuote(e, "BookA", MarketType.TOTALS, "over", 5.0, line=2.25),  # quarter line, unsupported
        OddsQuote(e, "BookA", MarketType.CORRECT_SCORE, "1-0", 7.0),  # exotic, out of scope here
    ]
    assert build_parlay_candidates(quotes) == []


def _generous_three_match_setup():
    """Three independent matches. 'Sharp' prices each near-fair; 'Soft'
    prices one side of each match noticeably above Sharp's -- an
    obviously +EV parlay if you combine Soft's generous side across all
    three matches.
    """
    quotes = []
    for name, sharp_home, soft_home in [("MatchA", 1.95, 2.30), ("MatchB", 1.95, 2.30), ("MatchC", 1.95, 2.30)]:
        e = _event(name)
        quotes.append(OddsQuote(e, "Sharp", MarketType.MONEYLINE_2WAY, "home", sharp_home))
        quotes.append(OddsQuote(e, "Sharp", MarketType.MONEYLINE_2WAY, "away", 1.95))
        quotes.append(OddsQuote(e, "Soft", MarketType.MONEYLINE_2WAY, "home", soft_home))
        quotes.append(OddsQuote(e, "Soft", MarketType.MONEYLINE_2WAY, "away", 1.70))
    return quotes


def test_find_parlay_value_flags_generous_cross_match_combo():
    candidates = build_parlay_candidates(_generous_three_match_setup())
    results = find_parlay_value(candidates, min_legs=2, max_legs=3, min_edge_percent=1.0)

    assert results  # something was found
    top = results[0]
    # A real parlay ticket is single-book: every leg in one result must
    # come from the same bookmaker.
    assert len({leg.bookmaker for leg in top.legs}) == 1
    # Cross-match only: every leg must come from a different event.
    assert len({leg.event_key for leg in top.legs}) == len(top.legs)
    assert len(top.legs) == 3  # the 3-leg combo has the highest compounded edge
    assert top.edge_percent > 0
    assert top.combined_odds == pytest.approx(
        top.legs[0].decimal_odds * top.legs[1].decimal_odds * top.legs[2].decimal_odds
    )
    # Results are ranked richest-edge first.
    assert results == sorted(results, key=lambda r: r.edge_percent, reverse=True)


def test_find_parlay_value_never_combines_two_legs_from_the_same_match():
    e = _event("A")
    quotes = [
        OddsQuote(e, "Soft", MarketType.MONEYLINE_2WAY, "home", 2.50),
        OddsQuote(e, "Soft", MarketType.MONEYLINE_2WAY, "away", 2.50),
        OddsQuote(e, "Sharp", MarketType.MONEYLINE_2WAY, "home", 1.95),
        OddsQuote(e, "Sharp", MarketType.MONEYLINE_2WAY, "away", 1.95),
    ]
    candidates = build_parlay_candidates(quotes)
    # Only one match exists, so no >=2-leg cross-match parlay can be built at all.
    results = find_parlay_value(candidates, min_legs=2, min_edge_percent=-100.0)
    assert results == []


def test_find_parlay_value_excludes_longshot_legs_below_threshold():
    quotes = _generous_three_match_setup()
    e = _event("Longshot")
    # A very unlikely leg (fair probability far below MIN_LEG_FAIR_PROBABILITY).
    quotes.append(OddsQuote(e, "Soft", MarketType.MONEYLINE_2WAY, "home", 50.0))
    quotes.append(OddsQuote(e, "Soft", MarketType.MONEYLINE_2WAY, "away", 1.02))
    quotes.append(OddsQuote(e, "Sharp", MarketType.MONEYLINE_2WAY, "home", 45.0))
    quotes.append(OddsQuote(e, "Sharp", MarketType.MONEYLINE_2WAY, "away", 1.03))

    candidates = build_parlay_candidates(quotes)
    longshot_home = next(c for c in candidates if c.event_label.startswith("Longshot") and c.selection == "home")
    assert longshot_home.fair_probability < MIN_LEG_FAIR_PROBABILITY

    results = find_parlay_value(candidates, min_legs=2, max_legs=4, min_edge_percent=1.0)
    assert results  # the other 3 matches' generous legs still combine fine
    # The longshot ("home", fair prob ~2%) must never appear in a result;
    # every leg actually used must clear the minimum fair-probability bar.
    assert all(leg.fair_probability >= MIN_LEG_FAIR_PROBABILITY for r in results for leg in r.legs)
    assert all(not (leg.event_label.startswith("Longshot") and leg.selection == "home") for r in results for leg in r.legs)


def test_find_parlay_value_needs_at_least_two_independent_bookmakers():
    """With only one bookmaker pricing every leg, its own devigged fair
    probability is used as the reference too, so every leg's edge is
    always <= 0 (the book's own margin) and compounding several such legs
    only makes the combined edge more negative -- never a genuine find.
    This mirrors why single-bookmaker arbitrage never fires either.
    """
    quotes = []
    for name in ["MatchA", "MatchB", "MatchC"]:
        e = _event(name)
        quotes.append(OddsQuote(e, "OnlyBook", MarketType.MONEYLINE_2WAY, "home", 1.95))
        quotes.append(OddsQuote(e, "OnlyBook", MarketType.MONEYLINE_2WAY, "away", 1.95))

    candidates = build_parlay_candidates(quotes)
    results = find_parlay_value(candidates, min_legs=2, min_edge_percent=0.01)
    assert results == []
