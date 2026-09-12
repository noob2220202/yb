from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.core.enums import MarketType, Sport
from app.core.schemas import NormalizedEvent, OddsQuote
from app.db.models import ArbitrageOpportunity, OddsSnapshot, ParlayValueFind, ValueEdge
from app.engine.scanner import run_scan_cycle, scan_arbitrage, scan_parlay_value, scan_value_edges, store_quotes
from tests.fixtures import demo_quotes


@pytest.mark.asyncio
async def test_full_scan_cycle_persists_snapshots_arbs_and_edges(db_session):
    quotes = demo_quotes([Sport.SOCCER, Sport.BASKETBALL])

    opportunities, edges, parlay_finds = await run_scan_cycle(db_session, quotes, source="test")

    # 3 arbitrage-eligible markets in the fixture: soccer 3-way ML, soccer
    # totals 2.5, soccer AH -0.5, plus basketball 2-way ML.
    assert len(opportunities) == 4
    assert all(o.margin_percent > 0 for o in opportunities)

    # The deliberately generous correct-score price should be flagged.
    assert len(edges) >= 1
    assert any(e.selection == "2-1" and e.bookmaker == "FixtureBookSoft" for e in edges)
    # ...and so should the same-bookmaker (FixtureBookA-only) cross-line
    # mispricing on Totals 3.5, while its fair-priced siblings (Totals 1.5,
    # Asian Handicap -1.5) should NOT be.
    assert any(e.market == "totals" and e.line == 3.5 and e.bookmaker == "FixtureBookA" for e in edges)
    assert not any(e.line == 1.5 for e in edges)
    assert not any(e.market == "asian_handicap" and e.line == -1.5 for e in edges)

    snapshot_count = (await db_session.execute(select(OddsSnapshot))).scalars().all()
    assert len(snapshot_count) == len(quotes)

    stored_opportunities = (await db_session.execute(select(ArbitrageOpportunity))).scalars().all()
    assert len(stored_opportunities) == 4

    stored_edges = (await db_session.execute(select(ValueEdge))).scalars().all()
    assert len(stored_edges) == len(edges)


@pytest.mark.asyncio
async def test_rerunning_scan_does_not_dedupe_but_reflects_latest_odds(db_session):
    quotes = demo_quotes([Sport.SOCCER])
    await run_scan_cycle(db_session, quotes, source="test")
    opportunities, _, _ = await run_scan_cycle(db_session, quotes, source="test")
    assert len(opportunities) == 3  # this cycle's own detections, independent of the previous run

    all_stored = (await db_session.execute(select(ArbitrageOpportunity))).scalars().all()
    assert len(all_stored) == 6  # two full cycles worth, kept as a history


@pytest.mark.asyncio
async def test_single_bookmaker_finds_value_edges_but_never_arbitrage(db_session):
    """Regression/spec test for 'Pinnacle only' usage: with every quote
    coming from exactly one bookmaker, the arbitrage engine must find
    nothing (a single book's own market can't be a same-book arbitrage —
    see app/engine/arbitrage.py), while the cross-line consistency scan
    still works, since it only ever needs one book's own multiple lines.
    """
    event = NormalizedEvent(
        sport=Sport.SOCCER,
        home_team="Solo",
        away_team="Book",
        commence_time=datetime(2026, 3, 1, tzinfo=timezone.utc),
        league="Test",
    )
    BOOK = "Pinnacle"
    quotes = [
        OddsQuote(event, BOOK, MarketType.MONEYLINE_3WAY, "home", 1.50),
        OddsQuote(event, BOOK, MarketType.MONEYLINE_3WAY, "draw", 4.50),
        OddsQuote(event, BOOK, MarketType.MONEYLINE_3WAY, "away", 6.50),
        OddsQuote(event, BOOK, MarketType.TOTALS, "over", 1.90, line=2.5),
        OddsQuote(event, BOOK, MarketType.TOTALS, "under", 1.95, line=2.5),
        # Same book's own secondary line, deliberately mispriced relative
        # to its own primary-line-calibrated model (fair odds ~3.50).
        OddsQuote(event, BOOK, MarketType.TOTALS, "over", 4.72, line=3.5),
    ]

    await store_quotes(db_session, quotes, source="test")

    opportunities = await scan_arbitrage(db_session, quotes)
    assert opportunities == []  # one bookmaker alone can never self-arbitrage

    edges = await scan_value_edges(db_session, quotes)
    assert len(edges) == 1
    assert edges[0].bookmaker == BOOK
    assert edges[0].market == "totals"
    assert edges[0].line == 3.5
    assert edges[0].edge_percent > 2.0


@pytest.mark.asyncio
async def test_scan_value_edges_isolates_one_broken_events_calibration(db_session, monkeypatch):
    """One event's odds somehow breaking the scoreline model (a scipy
    numerical edge case, bad data, ...) must not cost every other event's
    value edges in the same cycle.
    """
    quotes = demo_quotes([Sport.SOCCER])
    await store_quotes(db_session, quotes, source="test")

    import app.engine.scanner as scanner_module

    real_calibrate = scanner_module.scoreline_model.calibrate
    call_count = 0

    def flaky_calibrate(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("simulated calibration failure")
        return real_calibrate(*args, **kwargs)

    monkeypatch.setattr(scanner_module.scoreline_model, "calibrate", flaky_calibrate)

    edges = await scan_value_edges(db_session, quotes)
    assert call_count == 2  # Arsenal-Chelsea's calibration attempt failed, City-Newcastle's still ran
    # City-Newcastle's edges (correct score + the Totals 3.5 cross-line one) survive...
    assert any(e.selection == "2-1" for e in edges)
    assert any(e.market == "totals" and e.line == 3.5 for e in edges)
    # ...but Arsenal-Chelsea's own cross-line edges (its Asian Handicap -0.5
    # price vs. its own model) do NOT appear, since that event's whole
    # calibration attempt raised and was skipped.
    assert not any(e.market == "asian_handicap" for e in edges)


@pytest.mark.asyncio
async def test_scan_arbitrage_isolates_one_broken_groups_math(db_session, monkeypatch):
    quotes = demo_quotes([Sport.SOCCER, Sport.BASKETBALL])
    await store_quotes(db_session, quotes, source="test")

    import app.engine.scanner as scanner_module

    real_find_arbitrage = scanner_module.find_arbitrage
    call_count = 0

    def flaky_find_arbitrage(group):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("simulated arbitrage math failure")
        return real_find_arbitrage(group)

    monkeypatch.setattr(scanner_module, "find_arbitrage", flaky_find_arbitrage)

    opportunities = await scan_arbitrage(db_session, quotes)
    # 9 non-exotic (event, market, line) groups exist in this fixture; the
    # first one raises and is skipped (losing that one real arbitrage), but
    # every other group must still get scanned rather than the whole call
    # aborting.
    assert call_count == 9
    assert len(opportunities) == 3


@pytest.mark.asyncio
async def test_scan_parlay_value_persists_a_cross_match_find(db_session):
    """End-to-end persistence check for the parlay scanner: a clearly
    generous book across 3 independent matches should produce a stored
    ParlayValueFind with correct legs_json. Math itself is covered
    exhaustively in tests/test_parlay.py -- this just proves the DB wiring.
    """
    quotes = []
    for name, sharp_home, soft_home in [("MatchA", 1.95, 2.30), ("MatchB", 1.95, 2.30), ("MatchC", 1.95, 2.30)]:
        event = NormalizedEvent(
            sport=Sport.SOCCER,
            home_team=name,
            away_team="Away",
            commence_time=datetime(2026, 4, 1, tzinfo=timezone.utc),
            league="Test",
        )
        quotes.append(OddsQuote(event, "Sharp", MarketType.MONEYLINE_2WAY, "home", sharp_home))
        quotes.append(OddsQuote(event, "Sharp", MarketType.MONEYLINE_2WAY, "away", 1.95))
        quotes.append(OddsQuote(event, "Soft", MarketType.MONEYLINE_2WAY, "home", soft_home))
        quotes.append(OddsQuote(event, "Soft", MarketType.MONEYLINE_2WAY, "away", 1.70))

    await store_quotes(db_session, quotes, source="test")
    finds = await scan_parlay_value(db_session, quotes)

    assert len(finds) >= 1
    stored = (await db_session.execute(select(ParlayValueFind))).scalars().all()
    assert len(stored) == len(finds)
    assert all(f.edge_percent > 0 for f in finds)

    import json

    legs = json.loads(finds[0].legs_json)
    assert len(legs) >= 2
    assert {leg["event_label"] for leg in legs} <= {"MatchA vs Away", "MatchB vs Away", "MatchC vs Away"}
    assert all(leg["bookmaker"] == finds[0].bookmaker for leg in legs)
