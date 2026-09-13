"""Odds ingestion + the match browser's read queries.

Persists every incoming provider quote as an append-only snapshot (odds
history, replayable/auditable), and answers "what are the latest odds
for upcoming matches" for the hedge-box builder UI. No automatic
opportunity detection lives here any more -- picking which two
selections to hedge is a manual, per-match admin decision now (see
app/engine/hedgebox.py).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.schemas import NormalizedEvent, OddsQuote
from app.db.models import Bookmaker, Event, OddsSnapshot

# How far back a snapshot may be and still count as "current" for the
# match browser -- stale enough to suggest the provider stopped quoting
# this selection, so better to omit it than show a live-looking price
# that's actually hours old.
MAX_SNAPSHOT_AGE = timedelta(minutes=30)


async def get_or_create_event(session: AsyncSession, normalized: NormalizedEvent) -> Event:
    result = await session.execute(select(Event).where(Event.event_key == normalized.event_key))
    event = result.scalar_one_or_none()
    if event is not None:
        return event
    event = Event(
        event_key=normalized.event_key,
        sport=normalized.sport.value,
        league=normalized.league,
        home_team=normalized.home_team,
        away_team=normalized.away_team,
        commence_time=normalized.commence_time,
    )
    session.add(event)
    await session.flush()
    return event


async def get_or_create_bookmaker(session: AsyncSession, name: str, source: str) -> Bookmaker:
    result = await session.execute(select(Bookmaker).where(Bookmaker.name == name))
    bookmaker = result.scalar_one_or_none()
    if bookmaker is not None:
        return bookmaker
    bookmaker = Bookmaker(name=name, source=source)
    session.add(bookmaker)
    await session.flush()
    return bookmaker


async def store_quotes(session: AsyncSession, quotes: list[OddsQuote], source: str) -> None:
    for quote in quotes:
        event = await get_or_create_event(session, quote.event)
        bookmaker = await get_or_create_bookmaker(session, quote.bookmaker, source)
        session.add(
            OddsSnapshot(
                event_id=event.id,
                bookmaker_id=bookmaker.id,
                market=quote.market.value,
                line=quote.line,
                selection=quote.selection,
                decimal_odds=quote.decimal_odds,
                fetched_at=quote.fetched_at,
            )
        )
    await session.commit()


async def get_upcoming_events(
    session: AsyncSession,
    hours_ahead: float = 72.0,
    sport: str | None = None,
) -> list[Event]:
    """Matches from just-started (a bit of slack for in-play) through the
    lookahead window, soonest first."""
    now = datetime.now(timezone.utc)
    stmt = (
        select(Event)
        .where(Event.commence_time >= now - timedelta(hours=3))
        .where(Event.commence_time <= now + timedelta(hours=hours_ahead))
        .order_by(Event.commence_time.asc())
    )
    if sport:
        stmt = stmt.where(Event.sport == sport)
    return list((await session.execute(stmt)).scalars().all())


@dataclass(frozen=True)
class LatestQuote:
    market: str
    line: float | None
    selection: str
    decimal_odds: float
    bookmaker: str
    fetched_at: datetime


async def get_latest_quotes_by_event(
    session: AsyncSession, event_ids: list[int]
) -> dict[int, list[LatestQuote]]:
    """The newest snapshot per (event, market, line, selection), for
    events in ``event_ids`` -- reduced in Python rather than a NULL-
    tricky SQL self-join, since ``line`` is NULL for most markets and
    the snapshot volume for a bounded set of upcoming events is small.
    """
    if not event_ids:
        return {}

    cutoff = datetime.now(timezone.utc) - MAX_SNAPSHOT_AGE
    stmt = (
        select(OddsSnapshot, Bookmaker.name)
        .join(Bookmaker, OddsSnapshot.bookmaker_id == Bookmaker.id)
        .where(OddsSnapshot.event_id.in_(event_ids))
        .where(OddsSnapshot.fetched_at >= cutoff)
        .order_by(OddsSnapshot.fetched_at.desc())
    )
    rows = (await session.execute(stmt)).all()

    seen: set[tuple[int, str, float | None, str]] = set()
    by_event: dict[int, list[LatestQuote]] = defaultdict(list)
    for snapshot, bookmaker_name in rows:
        key = (snapshot.event_id, snapshot.market, snapshot.line, snapshot.selection)
        if key in seen:
            continue  # already saw a more recent snapshot for this exact key (rows are newest-first)
        seen.add(key)
        by_event[snapshot.event_id].append(
            LatestQuote(
                market=snapshot.market,
                line=snapshot.line,
                selection=snapshot.selection,
                decimal_odds=snapshot.decimal_odds,
                bookmaker=bookmaker_name,
                fetched_at=snapshot.fetched_at,
            )
        )
    return by_event
