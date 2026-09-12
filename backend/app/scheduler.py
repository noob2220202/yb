import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.enums import Sport
from app.core.schemas import OddsQuote
from app.db.models import ArbitrageOpportunity, Event, ValueEdge
from app.db.session import get_session_maker
from app.engine.scanner import run_scan_cycle
from app.notifications.telegram import (
    format_digest,
    format_pick_box,
    format_value_edge_digest,
    format_value_edge_box,
    send_telegram_message,
)
from app.providers.base import OddsProvider
from app.providers.oddsapi import OddsApiProvider
from app.providers.pinnacle import PinnacleProvider

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None

# Which (event, market, line[, selection, bookmaker]) combos we've already
# alerted on, so a still-active opportunity/edge doesn't re-fire every poll
# interval. Process-lifetime only — fine for a single-instance scaffold; a
# multi-worker deployment would need this moved into the DB or a shared
# cache.
_notified_keys: set[str] = set()
_notified_edge_keys: set[str] = set()


def build_providers() -> list[OddsProvider]:
    """Real providers only. Each is a no-op (``is_configured`` False) until
    its credentials are set, so with nothing configured yet, polling is
    simply a no-op and the dashboard correctly shows an empty state rather
    than any fabricated data.
    """
    settings = get_settings()
    return [
        PinnacleProvider(settings.pinnacle_base_url, settings.pinnacle_username, settings.pinnacle_password),
        OddsApiProvider(settings.odds_api_base_url, settings.odds_api_key, settings.odds_api_sport_keys_list),
    ]


def _active_sports() -> list[Sport]:
    settings = get_settings()
    sports = []
    for raw in settings.poll_sports_list:
        try:
            sports.append(Sport(raw))
        except ValueError:
            logger.warning("unknown sport in POLL_SPORTS: %s", raw)
    return sports


async def _collect_quotes(sports: list[Sport]) -> list[OddsQuote]:
    """Fetches every configured provider and merges the results into one
    list. This matters for correctness, not just convenience: real
    cross-bookmaker arbitrage can only be found when quotes from
    different providers (e.g. Pinnacle + The Odds API) are compared
    against each other in the *same* scan — scanning each provider in
    isolation would silently miss exactly the opportunities this product
    exists to find.
    """
    all_quotes: list[OddsQuote] = []
    for provider in build_providers():
        if not provider.is_configured:
            continue
        try:
            quotes = await provider.fetch(sports)
        except Exception:
            logger.exception("provider %s failed to fetch odds", provider.name)
            continue
        all_quotes.extend(quotes)
    return all_quotes


def _opportunity_key(opp: ArbitrageOpportunity) -> str:
    return f"{opp.event_id}|{opp.market}|{opp.line}"


async def _notify_new_opportunities(session: AsyncSession, opportunities: list[ArbitrageOpportunity]) -> None:
    settings = get_settings()
    if not settings.telegram_configured or not opportunities:
        return

    current_keys = {_opportunity_key(o) for o in opportunities}
    fresh = [
        o
        for o in opportunities
        if _opportunity_key(o) not in _notified_keys and o.margin_percent >= settings.telegram_min_margin_percent
    ]
    _notified_keys.clear()
    _notified_keys.update(current_keys)
    if not fresh:
        return

    boxes = []
    for i, opp in enumerate(fresh, start=1):
        event = await session.get(Event, opp.event_id)
        if event is None:
            continue
        boxes.append(format_pick_box(i, event, opp))
    if not boxes:
        return

    await send_telegram_message(format_digest(boxes))


def _value_edge_key(edge: ValueEdge) -> str:
    return f"{edge.event_id}|{edge.market}|{edge.line}|{edge.selection}|{edge.bookmaker}"


async def _notify_new_value_edges(session: AsyncSession, edges: list[ValueEdge]) -> None:
    """Separate Telegram channel for value edges (exotic markets, and the
    same-book cross-line consistency check) — the one signal that still
    fires when only Pinnacle is configured, since true arbitrage needs a
    second independent bookmaker. Always framed as NOT guaranteed.
    """
    settings = get_settings()
    if not settings.telegram_configured or not edges:
        return

    current_keys = {_value_edge_key(e) for e in edges}
    fresh = [
        e for e in edges if _value_edge_key(e) not in _notified_edge_keys and e.edge_percent >= settings.telegram_min_edge_percent
    ]
    _notified_edge_keys.clear()
    _notified_edge_keys.update(current_keys)
    if not fresh:
        return

    boxes = []
    for i, edge in enumerate(fresh, start=1):
        event = await session.get(Event, edge.event_id)
        if event is None:
            continue
        boxes.append(format_value_edge_box(i, event, edge))
    if not boxes:
        return

    await send_telegram_message(format_value_edge_digest(boxes))


async def poll_and_scan() -> tuple[list[ArbitrageOpportunity], list[ValueEdge]]:
    sports = _active_sports()
    quotes = await _collect_quotes(sports)
    if not quotes:
        return [], []

    async with get_session_maker()() as session:
        opportunities, edges = await run_scan_cycle(session, quotes, source="scheduled_poll")
        await _notify_new_opportunities(session, opportunities)
        await _notify_new_value_edges(session, edges)

    return opportunities, edges


def start_scheduler() -> AsyncIOScheduler:
    global _scheduler
    settings = get_settings()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(poll_and_scan, "interval", seconds=settings.poll_interval_seconds, id="poll_and_scan")
    scheduler.start()
    _scheduler = scheduler
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
