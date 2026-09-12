import pytest

from app.core.enums import Sport
from app.db.models import ArbitrageOpportunity, OddsSnapshot, ValueEdge
from app.engine.scanner import run_scan_cycle
from app.providers.demo import DemoProvider
from sqlalchemy import select


@pytest.mark.asyncio
async def test_full_scan_cycle_persists_snapshots_arbs_and_edges(db_session):
    quotes = await DemoProvider().fetch([Sport.SOCCER, Sport.BASKETBALL])

    opportunities, edges = await run_scan_cycle(db_session, quotes, source="demo")

    # 3 arbitrage-eligible markets in the fixture: soccer 3-way ML, soccer
    # totals 2.5, soccer AH -0.5, plus basketball 2-way ML.
    assert len(opportunities) == 4
    assert all(o.margin_percent > 0 for o in opportunities)

    # The deliberately generous correct-score price should be flagged.
    assert len(edges) >= 1
    assert any(e.selection == "2-1" and e.bookmaker == "DemoBookSoft" for e in edges)

    snapshot_count = (await db_session.execute(select(OddsSnapshot))).scalars().all()
    assert len(snapshot_count) == len(quotes)

    stored_opportunities = (await db_session.execute(select(ArbitrageOpportunity))).scalars().all()
    assert len(stored_opportunities) == 4

    stored_edges = (await db_session.execute(select(ValueEdge))).scalars().all()
    assert len(stored_edges) == len(edges)


@pytest.mark.asyncio
async def test_rerunning_scan_does_not_dedupe_but_reflects_latest_odds(db_session):
    quotes = await DemoProvider().fetch([Sport.SOCCER])
    await run_scan_cycle(db_session, quotes, source="demo")
    opportunities, _ = await run_scan_cycle(db_session, quotes, source="demo")
    assert len(opportunities) == 3  # this cycle's own detections, independent of the previous run

    all_stored = (await db_session.execute(select(ArbitrageOpportunity))).scalars().all()
    assert len(all_stored) == 6  # two full cycles worth, kept as a history
