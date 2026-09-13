import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import get_settings
from app.core.enums import Sport
from app.core.schemas import OddsQuote
from app.db.session import get_session_maker
from app.engine.scanner import store_quotes
from app.providers.base import OddsProvider
from app.providers.oddsapi import OddsApiProvider
from app.providers.pinnacle import PinnacleProvider

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def build_providers() -> list[OddsProvider]:
    """Real providers only. Each is a no-op (``is_configured`` False) until
    its credentials are set, so with nothing configured yet, polling is
    simply a no-op and the match browser correctly shows an empty state
    rather than any fabricated data.
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
    list, so the match browser can show whichever provider actually
    quotes each match/market."""
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


async def poll_and_ingest() -> int:
    """Fetches every configured provider and persists the quotes as odds
    snapshots. Returns how many quotes were stored this cycle."""
    sports = _active_sports()
    quotes = await _collect_quotes(sports)
    if not quotes:
        return 0

    async with get_session_maker()() as session:
        await store_quotes(session, quotes, source="scheduled_poll")

    return len(quotes)


def start_scheduler() -> AsyncIOScheduler:
    global _scheduler
    settings = get_settings()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(poll_and_ingest, "interval", seconds=settings.poll_interval_seconds, id="poll_and_ingest")
    scheduler.start()
    _scheduler = scheduler
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
