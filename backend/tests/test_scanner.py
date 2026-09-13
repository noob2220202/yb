from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.enums import MarketType, Sport
from app.core.schemas import NormalizedEvent, OddsQuote
from app.db.models import Event, OddsSnapshot
from app.engine.scanner import get_latest_quotes_by_event, get_upcoming_events, store_quotes
from tests.fixtures import demo_quotes


@pytest.mark.asyncio
async def test_store_quotes_persists_one_snapshot_per_quote(db_session):
    quotes = demo_quotes([Sport.SOCCER, Sport.BASKETBALL])
    await store_quotes(db_session, quotes, source="test")

    snapshots = (await db_session.execute(select(OddsSnapshot))).scalars().all()
    assert len(snapshots) == len(quotes)

    events = (await db_session.execute(select(Event))).scalars().all()
    event_keys = {e.event_key for e in events}
    assert {q.event.event_key for q in quotes} == event_keys


@pytest.mark.asyncio
async def test_store_quotes_reuses_existing_event_and_bookmaker(db_session):
    quotes = demo_quotes([Sport.SOCCER])
    await store_quotes(db_session, quotes, source="test")
    await store_quotes(db_session, quotes, source="test")  # a second poll cycle, same odds

    events = (await db_session.execute(select(Event))).scalars().all()
    assert len(events) == len({q.event.event_key for q in quotes})  # not duplicated

    snapshots = (await db_session.execute(select(OddsSnapshot))).scalars().all()
    assert len(snapshots) == 2 * len(quotes)  # append-only history, both cycles kept


@pytest.mark.asyncio
async def test_get_upcoming_events_filters_by_window_and_sport(db_session):
    now = datetime.now(timezone.utc)
    soon = NormalizedEvent(sport=Sport.SOCCER, home_team="A", away_team="B", commence_time=now + timedelta(hours=2))
    far = NormalizedEvent(sport=Sport.SOCCER, home_team="C", away_team="D", commence_time=now + timedelta(days=30))
    past = NormalizedEvent(sport=Sport.SOCCER, home_team="E", away_team="F", commence_time=now - timedelta(hours=10))
    basketball = NormalizedEvent(sport=Sport.BASKETBALL, home_team="G", away_team="H", commence_time=now + timedelta(hours=2))

    for e in (soon, far, past, basketball):
        await store_quotes(
            db_session,
            [OddsQuote(e, "Book", MarketType.MONEYLINE_2WAY, "home", 1.9)],
            source="test",
        )

    events = await get_upcoming_events(db_session, hours_ahead=72.0)
    keys = {e.event_key for e in events}
    assert soon.event_key in keys
    assert basketball.event_key in keys
    assert far.event_key not in keys
    assert past.event_key not in keys

    soccer_only = await get_upcoming_events(db_session, hours_ahead=72.0, sport="soccer")
    assert {e.event_key for e in soccer_only} == {soon.event_key}


@pytest.mark.asyncio
async def test_get_latest_quotes_by_event_keeps_only_newest_per_selection(db_session):
    event = NormalizedEvent(
        sport=Sport.SOCCER,
        home_team="Arsenal",
        away_team="Chelsea",
        commence_time=datetime.now(timezone.utc) + timedelta(hours=6),
    )
    now = datetime.now(timezone.utc)
    old_quote = OddsQuote(event, "BookA", MarketType.MONEYLINE_3WAY, "home", 1.80, fetched_at=now - timedelta(minutes=5))
    new_quote = OddsQuote(event, "BookA", MarketType.MONEYLINE_3WAY, "home", 1.95, fetched_at=now)

    await store_quotes(db_session, [old_quote], source="test")
    await store_quotes(db_session, [new_quote], source="test")

    events = await get_upcoming_events(db_session, hours_ahead=72.0)
    by_event = await get_latest_quotes_by_event(db_session, [e.id for e in events])
    quotes = next(iter(by_event.values()))
    assert len(quotes) == 1
    assert quotes[0].decimal_odds == 1.95


@pytest.mark.asyncio
async def test_get_latest_quotes_by_event_ignores_stale_snapshots(db_session):
    event = NormalizedEvent(
        sport=Sport.SOCCER,
        home_team="Arsenal",
        away_team="Chelsea",
        commence_time=datetime.now(timezone.utc) + timedelta(hours=6),
    )
    stale = OddsQuote(
        event,
        "BookA",
        MarketType.MONEYLINE_3WAY,
        "home",
        1.80,
        fetched_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    await store_quotes(db_session, [stale], source="test")

    events = await get_upcoming_events(db_session, hours_ahead=72.0)
    by_event = await get_latest_quotes_by_event(db_session, [e.id for e in events])
    assert by_event == {}
