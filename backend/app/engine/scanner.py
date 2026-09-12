"""Orchestrates one poll cycle: persist incoming odds, then run both the
guaranteed-profit arbitrage scan (core markets) and the scoreline
value-edge scan (exotic markets) over them.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import MarketType
from app.core.schemas import NormalizedEvent, OddsQuote
from app.db.models import ArbitrageOpportunity, Bookmaker, Event, OddsSnapshot, ValueEdge
from app.engine import scoreline_model
from app.engine.arbitrage import find_arbitrage

MIN_VALUE_EDGE_PERCENT = 2.0
PREFERRED_TOTALS_LINE = 2.5


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


def group_by_market(quotes: list[OddsQuote]) -> dict[tuple[str, MarketType, float | None], list[OddsQuote]]:
    groups: dict[tuple[str, MarketType, float | None], list[OddsQuote]] = defaultdict(list)
    for q in quotes:
        groups[q.market_key].append(q)
    return groups


def group_by_event(quotes: list[OddsQuote]) -> dict[str, list[OddsQuote]]:
    groups: dict[str, list[OddsQuote]] = defaultdict(list)
    for q in quotes:
        groups[q.event.event_key].append(q)
    return groups


async def scan_arbitrage(session: AsyncSession, quotes: list[OddsQuote]) -> list[ArbitrageOpportunity]:
    """Groups quotes by (event, market, line) and persists every genuine,
    guaranteed-profit arbitrage found."""
    opportunities: list[ArbitrageOpportunity] = []
    for (event_key, market, _line), group in group_by_market(quotes).items():
        if market.is_exotic:
            continue  # exotic markets go through scan_value_edges instead
        result = find_arbitrage(group)
        if result is None or not result.is_arbitrage:
            continue

        event_result = await session.execute(select(Event).where(Event.event_key == event_key))
        event = event_result.scalar_one_or_none()
        if event is None:
            continue

        legs_json = json.dumps(
            [{"selection": leg.selection, "bookmaker": leg.bookmaker, "decimal_odds": leg.decimal_odds} for leg in result.legs]
        )
        opportunity = ArbitrageOpportunity(
            event_id=event.id,
            market=result.market.value,
            line=result.line,
            total_implied_probability=result.total_implied_probability,
            margin_percent=result.margin_percent,
            push_possible=result.push_possible,
            legs_json=legs_json,
            detected_at=datetime.now(timezone.utc),
        )
        session.add(opportunity)
        opportunities.append(opportunity)

    await session.commit()
    return opportunities


def _pick_calibration_inputs(
    quotes: list[OddsQuote],
) -> tuple[float, float, float, float, float, float] | None:
    """Finds a devigging-ready 1X2 + Totals price set to calibrate the
    scoreline model against, from whichever bookmaker has both. Returns
    (home_odds, draw_odds, away_odds, totals_line, over_odds, under_odds).
    """
    by_book_ml: dict[str, dict[str, float]] = defaultdict(dict)
    by_book_totals: dict[str, dict[float, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))

    for q in quotes:
        if q.market == MarketType.MONEYLINE_3WAY:
            by_book_ml[q.bookmaker][q.selection] = q.decimal_odds
        elif q.market == MarketType.TOTALS and q.line is not None:
            by_book_totals[q.bookmaker][q.line][q.selection] = q.decimal_odds

    for book, ml in by_book_ml.items():
        if not {"home", "draw", "away"}.issubset(ml.keys()):
            continue
        totals_for_book = by_book_totals.get(book, {})
        line = PREFERRED_TOTALS_LINE if PREFERRED_TOTALS_LINE in totals_for_book else next(iter(totals_for_book), None)
        if line is None:
            continue
        totals = totals_for_book[line]
        if not {"over", "under"}.issubset(totals.keys()):
            continue
        return ml["home"], ml["draw"], ml["away"], line, totals["over"], totals["under"]
    return None


async def scan_value_edges(session: AsyncSession, quotes: list[OddsQuote]) -> list[ValueEdge]:
    """Per event: calibrate the scoreline model from 1X2 + Totals, then
    flag two kinds of divergence from the model's own view:

    1. Exotic markets (correct score / winning margin) priced by any
       provider — needs a book that quotes those.
    2. Any book's OTHER Totals/Asian-Handicap lines on the same match —
       needs nothing but a book that quotes more than one line per match
       (Pinnacle always does). With only one provider configured, every
       quote compared is necessarily that same book's own price, so this
       becomes a same-book internal-consistency check for free — the one
       signal that still works with only a single provider.

    Skipped for events without enough core-market data to calibrate.
    """
    edges: list[ValueEdge] = []

    for event_key, event_quotes in group_by_event(quotes).items():
        calibration_inputs = _pick_calibration_inputs(event_quotes)
        if calibration_inputs is None:
            continue

        correct_score_quotes = [
            (q.bookmaker, q.selection, q.decimal_odds) for q in event_quotes if q.market == MarketType.CORRECT_SCORE
        ]
        margin_quotes = [
            (q.bookmaker, q.selection, q.decimal_odds) for q in event_quotes if q.market == MarketType.WINNING_MARGIN
        ]
        totals_quotes = [
            (q.bookmaker, q.line, q.selection, q.decimal_odds)
            for q in event_quotes
            if q.market == MarketType.TOTALS and q.line is not None
        ]
        handicap_quotes = [
            (q.bookmaker, q.line, q.selection, q.decimal_odds)
            for q in event_quotes
            if q.market == MarketType.ASIAN_HANDICAP and q.line is not None
        ]

        calibrated = scoreline_model.calibrate(*calibration_inputs)
        calibration_totals_line = calibration_inputs[3]

        exotic_found = scoreline_model.find_value_edges(
            calibrated.matrix, correct_score_quotes, margin_quotes, min_edge_percent=MIN_VALUE_EDGE_PERCENT
        )
        cross_line_found = scoreline_model.find_cross_line_edges(
            calibrated.matrix,
            totals_quotes,
            handicap_quotes,
            calibration_totals_line=calibration_totals_line,
            min_edge_percent=MIN_VALUE_EDGE_PERCENT,
        )
        if not exotic_found and not cross_line_found:
            continue

        event_result = await session.execute(select(Event).where(Event.event_key == event_key))
        event = event_result.scalar_one_or_none()
        if event is None:
            continue

        for edge in exotic_found:
            market = MarketType.CORRECT_SCORE if "-" in edge.selection else MarketType.WINNING_MARGIN
            record = ValueEdge(
                event_id=event.id,
                market=market.value,
                line=None,
                selection=edge.selection,
                bookmaker=edge.bookmaker,
                quoted_decimal_odds=edge.decimal_odds,
                model_probability=edge.model_probability,
                implied_probability=edge.implied_probability,
                edge_percent=edge.edge_percent,
                detected_at=datetime.now(timezone.utc),
            )
            session.add(record)
            edges.append(record)

        for edge in cross_line_found:
            record = ValueEdge(
                event_id=event.id,
                market=edge.market,
                line=edge.line,
                selection=edge.selection,
                bookmaker=edge.bookmaker,
                quoted_decimal_odds=edge.decimal_odds,
                model_probability=edge.model_probability,
                implied_probability=edge.implied_probability,
                edge_percent=edge.edge_percent,
                detected_at=datetime.now(timezone.utc),
            )
            session.add(record)
            edges.append(record)

    await session.commit()
    return edges


async def run_scan_cycle(session: AsyncSession, quotes: list[OddsQuote], source: str) -> tuple[list[ArbitrageOpportunity], list[ValueEdge]]:
    await store_quotes(session, quotes, source)
    opportunities = await scan_arbitrage(session, quotes)
    edges = await scan_value_edges(session, quotes)
    return opportunities, edges
