import pytest

import app.scheduler as scheduler_module
from app.core.enums import MarketType, Sport
from app.core.schemas import OddsQuote
from app.db.models import OddsSnapshot
from app.providers.base import OddsProvider
from tests.fixtures import ARSENAL_CHELSEA


class FakeProvider(OddsProvider):
    def __init__(self, name: str, quotes: list[OddsQuote]):
        self.name = name
        self._quotes = quotes

    async def fetch(self, sports: list[Sport]) -> list[OddsQuote]:
        return self._quotes


@pytest.mark.asyncio
async def test_collect_quotes_merges_all_configured_providers(monkeypatch):
    q1 = OddsQuote(ARSENAL_CHELSEA, "BookX", MarketType.MONEYLINE_3WAY, "home", 2.0)
    q2 = OddsQuote(ARSENAL_CHELSEA, "BookY", MarketType.MONEYLINE_3WAY, "away", 3.0)
    monkeypatch.setattr(
        scheduler_module, "build_providers", lambda: [FakeProvider("p1", [q1]), FakeProvider("p2", [q2])]
    )

    quotes = await scheduler_module._collect_quotes([Sport.SOCCER])
    assert quotes == [q1, q2]


@pytest.mark.asyncio
async def test_collect_quotes_skips_unconfigured_and_failing_providers(monkeypatch):
    class BrokenProvider(OddsProvider):
        name = "broken"

        async def fetch(self, sports):
            raise RuntimeError("boom")

    class UnconfiguredProvider(OddsProvider):
        name = "unconfigured"

        @property
        def is_configured(self) -> bool:
            return False

        async def fetch(self, sports):
            raise AssertionError("should never be called")

    good_quote = OddsQuote(ARSENAL_CHELSEA, "BookX", MarketType.MONEYLINE_3WAY, "home", 2.0)
    monkeypatch.setattr(
        scheduler_module,
        "build_providers",
        lambda: [BrokenProvider(), UnconfiguredProvider(), FakeProvider("good", [good_quote])],
    )

    quotes = await scheduler_module._collect_quotes([Sport.SOCCER])
    assert quotes == [good_quote]


@pytest.mark.asyncio
async def test_poll_and_ingest_stores_quotes_and_returns_count(db_session, monkeypatch):
    q1 = OddsQuote(ARSENAL_CHELSEA, "BookX", MarketType.MONEYLINE_3WAY, "home", 2.0)
    q2 = OddsQuote(ARSENAL_CHELSEA, "BookX", MarketType.MONEYLINE_3WAY, "draw", 3.5)
    monkeypatch.setattr(scheduler_module, "build_providers", lambda: [FakeProvider("p1", [q1, q2])])

    # AsyncSession is a context manager; wrap the shared test session so
    # `async with get_session_maker()()` in poll_and_ingest works without
    # a real engine.
    class _CtxWrapper:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(scheduler_module, "get_session_maker", lambda: (lambda: _CtxWrapper()))

    count = await scheduler_module.poll_and_ingest()
    assert count == 2

    snapshots = (await db_session.execute(OddsSnapshot.__table__.select())).all()
    assert len(snapshots) == 2


@pytest.mark.asyncio
async def test_poll_and_ingest_returns_zero_when_nothing_fetched(monkeypatch):
    monkeypatch.setattr(scheduler_module, "build_providers", lambda: [FakeProvider("p1", [])])
    count = await scheduler_module.poll_and_ingest()
    assert count == 0
