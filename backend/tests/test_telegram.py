from datetime import datetime, timezone

import httpx
import pytest

from app.notifications.telegram import build_hedge_box_keyboard, format_hedge_box, send_telegram_message


def test_format_hedge_box_contains_key_fields():
    text = format_hedge_box(
        box_id=12,
        event="Crystal Palace vs Ipswich",
        league="Premier League",
        commence_time=datetime(2026, 9, 12, 14, 0, tzinfo=timezone.utc),
        leg_a_label="무승부",
        leg_a_odds=3.75,
        leg_a_stake=12800,
        leg_b_label="크리스탈 팰리스 -0.5",
        leg_b_odds=1.909,
        leg_b_stake=25100,
        total_stake=37900,
        guaranteed_profit=10016,
        profit_percent=26.4,
        implied_hit_rate_percent=None,
    )
    assert "BOX #12" in text
    assert "Crystal Palace vs Ipswich" in text
    assert "무승부 @ 3.75" in text
    assert "12,800원" in text
    assert "37,900원" in text
    assert "10,016원" in text
    assert "확정 수익이 아닙니다" in text


def test_format_hedge_box_includes_hit_rate_line_when_given():
    text = format_hedge_box(
        box_id=1,
        event="A vs B",
        league="",
        commence_time=datetime.now(timezone.utc),
        leg_a_label="A",
        leg_a_odds=2.0,
        leg_a_stake=1000,
        leg_b_label="B",
        leg_b_odds=2.0,
        leg_b_stake=1000,
        total_stake=2000,
        guaranteed_profit=100,
        profit_percent=5.0,
        implied_hit_rate_percent=92.3,
    )
    assert "92.3%" in text


def test_build_hedge_box_keyboard_always_has_detail_button():
    kb = build_hedge_box_keyboard("https://example.com/box/1")
    assert kb["inline_keyboard"][0][0]["url"] == "https://example.com/box/1"
    assert len(kb["inline_keyboard"]) == 1


def test_build_hedge_box_keyboard_adds_guide_button_when_given():
    kb = build_hedge_box_keyboard("https://example.com/box/1", "https://example.com/guide")
    assert len(kb["inline_keyboard"]) == 2
    assert kb["inline_keyboard"][1][0]["url"] == "https://example.com/guide"


@pytest.mark.asyncio
async def test_send_telegram_message_noop_when_unconfigured(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    from app.config import get_settings

    get_settings.cache_clear()
    sent = await send_telegram_message("hello")
    assert sent is False
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_send_telegram_message_includes_reply_markup(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    from app.config import get_settings

    get_settings.cache_clear()

    captured = {}

    async def fake_post(self, url, json=None, **kwargs):
        captured["url"] = url
        captured["json"] = json
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"ok": True}, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    keyboard = build_hedge_box_keyboard("https://example.com/box/1")
    sent = await send_telegram_message("hello", reply_markup=keyboard)
    assert sent is True
    assert captured["json"]["reply_markup"] == keyboard

    get_settings.cache_clear()
