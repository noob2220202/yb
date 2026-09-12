import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import get_settings
from app.core.enums import Sport
from app.db.models import ArbitrageOpportunity, ValueEdge
from app.db.session import async_session_maker
from app.engine.scanner import run_scan_cycle
from app.providers.base import OddsProvider
from app.providers.demo import DemoProvider
from app.providers.oddsapi import OddsApiProvider
from app.providers.pinnacle import PinnacleProvider

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def build_providers() -> list[OddsProvider]:
    settings = get_settings()
    providers: list[OddsProvider] = []
    if settings.use_demo_provider:
        providers.append(DemoProvider())
    providers.append(PinnacleProvider(settings.pinnacle_base_url, settings.pinnacle_username, settings.pinnacle_password))
    providers.append(OddsApiProvider(settings.odds_api_base_url, settings.odds_api_key, settings.odds_api_sport_keys_list))
    return providers


def _active_sports() -> list[Sport]:
    settings = get_settings()
    sports = []
    for raw in settings.poll_sports_list:
        try:
            sports.append(Sport(raw))
        except ValueError:
            logger.warning("unknown sport in POLL_SPORTS: %s", raw)
    return sports


async def poll_and_scan() -> tuple[list[ArbitrageOpportunity], list[ValueEdge]]:
    sports = _active_sports()
    all_opportunities: list[ArbitrageOpportunity] = []
    all_edges: list[ValueEdge] = []

    async with async_session_maker() as session:
        for provider in build_providers():
            if not provider.is_configured:
                continue
            try:
                quotes = await provider.fetch(sports)
            except Exception:
                logger.exception("provider %s failed to fetch odds", provider.name)
                continue
            if not quotes:
                continue
            opportunities, edges = await run_scan_cycle(session, quotes, source=provider.name)
            all_opportunities.extend(opportunities)
            all_edges.extend(edges)

    return all_opportunities, all_edges


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
