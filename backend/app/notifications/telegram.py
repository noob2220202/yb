"""Telegram bot notifier for sent hedge boxes.

Server-side channel: as long as the backend process is running, a box
the admin exports gets pushed to a Telegram chat/channel. Setup: create
a bot via @BotFather, add it to the target channel as an admin, set
TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (see backend/.env.example).
Silently disabled if unset.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from app.config import get_settings

TELEGRAM_API_BASE = "https://api.telegram.org"

KST_OFFSET_HOURS = 9


def _format_kst(dt: datetime) -> str:
    from datetime import timedelta, timezone

    kst = dt.astimezone(timezone(timedelta(hours=KST_OFFSET_HOURS)))
    return kst.strftime("%m/%d(%a) %H:%M") + " KST"


def format_hedge_box(
    *,
    box_id: int,
    event: str,
    league: str,
    commence_time: datetime,
    leg_a_label: str,
    leg_a_odds: float,
    leg_a_stake: float,
    leg_b_label: str,
    leg_b_odds: float,
    leg_b_stake: float,
    total_stake: float,
    guaranteed_profit: float,
    profit_percent: float,
    implied_hit_rate_percent: float | None,
) -> str:
    league_part = f"{league} · " if league else ""
    hit_rate_line = (
        f"📊 예상 적중률 약 {implied_hit_rate_percent:.1f}% (제외된 결과 배당 기준 참고용)\n"
        if implied_hit_rate_percent is not None
        else ""
    )
    return (
        f"🐐 BOX #{box_id}\n"
        f"{league_part}{_format_kst(commence_time)}\n"
        f"⚽ {event}\n"
        "\n"
        f"① {leg_a_label} @ {leg_a_odds:.2f} → {leg_a_stake:,.0f}원\n"
        f"② {leg_b_label} @ {leg_b_odds:.2f} → {leg_b_stake:,.0f}원\n"
        "\n"
        f"💰 총 배팅 {total_stake:,.0f}원\n"
        f"✅ 적중 시 순손익 +{guaranteed_profit:,.0f}원 ({profit_percent:.1f}%)\n"
        f"{hit_rate_line}"
        "\n"
        "⚠️ 확정 수익이 아닙니다 — 두 다리 모두 빗나가면(제외된 결과가 실제로 나오면) "
        "베팅금 전액을 잃습니다."
    )


def build_hedge_box_keyboard(detail_url: str, guide_url: str | None = None) -> dict[str, Any]:
    row = [{"text": "📋 박스 상세보기", "url": detail_url}]
    buttons = [row]
    if guide_url:
        buttons.append([{"text": "📖 헤지 배팅이란?", "url": guide_url}])
    return {"inline_keyboard": buttons}


async def send_telegram_message(text: str, reply_markup: dict[str, Any] | None = None) -> bool:
    """Returns True if a send was attempted and didn't error, False if the
    channel isn't configured or the request failed (never raises — a
    notification failure must never break the calling request)."""
    settings = get_settings()
    if not settings.telegram_configured:
        return False
    url = f"{TELEGRAM_API_BASE}/bot{settings.telegram_bot_token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": settings.telegram_chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
        return True
    except httpx.HTTPError:
        return False
