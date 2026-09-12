import json
from datetime import datetime, timezone

import pytest

import app.scheduler as scheduler_module
from app.core.enums import MarketType, Sport
from app.core.schemas import OddsQuote
from app.db.models import ArbitrageOpportunity, Event, ValueEdge
from app.engine.scanner import run_scan_cycle
from app.notifications.telegram import (
    format_digest,
    format_pick_box,
    format_value_edge_box,
    format_value_edge_digest,
)
from app.providers.base import OddsProvider
from tests.fixtures import ARSENAL_CHELSEA


class FakeProvider(OddsProvider):
    def __init__(self, name: str, quotes: list[OddsQuote]):
        self.name = name
        self._quotes = quotes

    async def fetch(self, sports: list[Sport]) -> list[OddsQuote]:
        return self._quotes


def make_event(**overrides) -> Event:
    defaults = dict(
        event_key="soccer:a-vs-b:2026-01-01",
        sport="soccer",
        league="Test League",
        home_team="A",
        away_team="B",
        commence_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return Event(**defaults)


def make_opportunity(event_id: int, margin: float = 2.0, market: str = "moneyline_3way", line=None) -> ArbitrageOpportunity:
    return ArbitrageOpportunity(
        event_id=event_id,
        market=market,
        line=line,
        total_implied_probability=1 / (1 + margin / 100),
        margin_percent=margin,
        push_possible=False,
        legs_json=json.dumps([{"selection": "home", "bookmaker": "BookA", "decimal_odds": 2.1}]),
        detected_at=datetime.now(timezone.utc),
    )


def make_value_edge(
    event_id: int, edge_percent: float = 5.0, market: str = "totals", line: float | None = 3.5
) -> ValueEdge:
    return ValueEdge(
        event_id=event_id,
        market=market,
        line=line,
        selection="over",
        bookmaker="Pinnacle",
        quoted_decimal_odds=4.72,
        model_probability=0.286,
        implied_probability=0.212,
        edge_percent=edge_percent,
        detected_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------
# _collect_quotes: merging across providers
# ---------------------------------------------------------------------


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
async def test_merged_quotes_enable_cross_provider_arbitrage(db_session, monkeypatch):
    """Regression test for the bug where scanning each provider's quotes in
    isolation could never detect an arbitrage that requires combining a
    Pinnacle-only leg with an OddsAPI-only leg of the same market.
    """
    q_home = OddsQuote(ARSENAL_CHELSEA, "PinnacleBook", MarketType.MONEYLINE_3WAY, "home", 2.10)
    q_draw = OddsQuote(ARSENAL_CHELSEA, "OddsApiBookY", MarketType.MONEYLINE_3WAY, "draw", 3.60)
    q_away = OddsQuote(ARSENAL_CHELSEA, "OddsApiBookZ", MarketType.MONEYLINE_3WAY, "away", 4.50)
    monkeypatch.setattr(
        scheduler_module,
        "build_providers",
        lambda: [FakeProvider("pinnacle", [q_home]), FakeProvider("oddsapi", [q_draw, q_away])],
    )

    quotes = await scheduler_module._collect_quotes([Sport.SOCCER])
    opportunities, _ = await run_scan_cycle(db_session, quotes, source="test")

    assert len(opportunities) == 1
    assert opportunities[0].margin_percent > 0


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


# ---------------------------------------------------------------------
# Telegram formatting (pure functions, no network)
# ---------------------------------------------------------------------


def test_format_pick_box_contains_key_fields():
    event = make_event()
    opp = make_opportunity(event_id=1, margin=2.44, market="moneyline_3way")
    text = format_pick_box(1, event, opp)
    assert "A vs B" in text
    assert "+2.44%" in text
    assert "BookA" in text


def test_format_digest_wraps_all_boxes_with_count_header():
    text = format_digest(["box-one", "box-two"])
    assert text.startswith("🎯")
    assert "2건" in text
    assert "box-one" in text and "box-two" in text


# ---------------------------------------------------------------------
# _notify_new_opportunities: dedupe + threshold + configured gate
# ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notify_dedupes_still_active_opportunity(db_session, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    from app.config import get_settings

    get_settings.cache_clear()

    sent = []

    async def fake_send(text):
        sent.append(text)
        return True

    monkeypatch.setattr(scheduler_module, "send_telegram_message", fake_send)
    scheduler_module._notified_keys.clear()

    event = make_event()
    db_session.add(event)
    await db_session.flush()
    opp = make_opportunity(event.id, margin=2.0)
    db_session.add(opp)
    await db_session.flush()

    await scheduler_module._notify_new_opportunities(db_session, [opp])
    assert len(sent) == 1
    assert "A vs B" in sent[0]

    # 다음 폴링에도 같은 (event, market, line) 조합이 그대로면 재알림하지 않는다
    await scheduler_module._notify_new_opportunities(db_session, [opp])
    assert len(sent) == 1

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_notify_skips_below_margin_threshold(db_session, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setenv("TELEGRAM_MIN_MARGIN_PERCENT", "5.0")
    from app.config import get_settings

    get_settings.cache_clear()

    sent = []

    async def fake_send(text):
        sent.append(text)
        return True

    monkeypatch.setattr(scheduler_module, "send_telegram_message", fake_send)
    scheduler_module._notified_keys.clear()

    event = make_event(event_key="soccer:c-vs-d:2026-01-01", home_team="C", away_team="D")
    db_session.add(event)
    await db_session.flush()
    opp = make_opportunity(event.id, margin=1.0)  # 5% 임계값 미달
    db_session.add(opp)
    await db_session.flush()

    await scheduler_module._notify_new_opportunities(db_session, [opp])
    assert sent == []

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_notify_noop_when_telegram_not_configured(db_session, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    from app.config import get_settings

    get_settings.cache_clear()

    called = False

    async def fake_send(text):
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(scheduler_module, "send_telegram_message", fake_send)
    scheduler_module._notified_keys.clear()

    event = make_event(event_key="soccer:e-vs-f:2026-01-01", home_team="E", away_team="F")
    db_session.add(event)
    await db_session.flush()
    opp = make_opportunity(event.id, margin=10.0)
    db_session.add(opp)
    await db_session.flush()

    await scheduler_module._notify_new_opportunities(db_session, [opp])
    assert called is False

    get_settings.cache_clear()


# ---------------------------------------------------------------------
# _notify_new_value_edges: same pattern, separate channel/threshold —
# this is the one that actually fires when only Pinnacle is configured.
# ---------------------------------------------------------------------


def test_format_value_edge_box_contains_key_fields():
    event = make_event()
    edge = make_value_edge(event_id=1, edge_percent=34.98, market="totals", line=3.5)
    text = format_value_edge_box(1, event, edge)
    assert "A vs B" in text
    assert "Pinnacle" in text
    assert "+35.0%" in text


def test_format_value_edge_digest_marks_not_guaranteed():
    text = format_value_edge_digest(["box-one"])
    assert "확정" in text and "아님" in text
    assert "box-one" in text


@pytest.mark.asyncio
async def test_notify_value_edges_dedupes_still_active(db_session, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    from app.config import get_settings

    get_settings.cache_clear()

    sent = []

    async def fake_send(text):
        sent.append(text)
        return True

    monkeypatch.setattr(scheduler_module, "send_telegram_message", fake_send)
    scheduler_module._notified_edge_keys.clear()

    event = make_event()
    db_session.add(event)
    await db_session.flush()
    edge = make_value_edge(event.id, edge_percent=10.0)
    db_session.add(edge)
    await db_session.flush()

    await scheduler_module._notify_new_value_edges(db_session, [edge])
    assert len(sent) == 1
    assert "A vs B" in sent[0]

    await scheduler_module._notify_new_value_edges(db_session, [edge])
    assert len(sent) == 1  # 같은 엣지가 그대로면 재알림하지 않는다

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_notify_value_edges_skips_below_edge_threshold(db_session, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setenv("TELEGRAM_MIN_EDGE_PERCENT", "20.0")
    from app.config import get_settings

    get_settings.cache_clear()

    sent = []

    async def fake_send(text):
        sent.append(text)
        return True

    monkeypatch.setattr(scheduler_module, "send_telegram_message", fake_send)
    scheduler_module._notified_edge_keys.clear()

    event = make_event(event_key="soccer:g-vs-h:2026-01-01", home_team="G", away_team="H")
    db_session.add(event)
    await db_session.flush()
    edge = make_value_edge(event.id, edge_percent=5.0)  # 20% 임계값 미달
    db_session.add(edge)
    await db_session.flush()

    await scheduler_module._notify_new_value_edges(db_session, [edge])
    assert sent == []

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_notify_value_edges_noop_when_telegram_not_configured(db_session, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    from app.config import get_settings

    get_settings.cache_clear()

    called = False

    async def fake_send(text):
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(scheduler_module, "send_telegram_message", fake_send)
    scheduler_module._notified_edge_keys.clear()

    event = make_event(event_key="soccer:i-vs-j:2026-01-01", home_team="I", away_team="J")
    db_session.add(event)
    await db_session.flush()
    edge = make_value_edge(event.id, edge_percent=50.0)
    db_session.add(edge)
    await db_session.flush()

    await scheduler_module._notify_new_value_edges(db_session, [edge])
    assert called is False

    get_settings.cache_clear()
