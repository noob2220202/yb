"""Telegram bot notifier.

Unlike the dashboard's browser notification (which only fires while that
tab is open), this is a server-side channel: as long as the backend
process is running, a new guaranteed-profit pick gets pushed to a
Telegram chat even if nobody has the site open. Setup: create a bot via
@BotFather, start a chat with it, set TELEGRAM_BOT_TOKEN /
TELEGRAM_CHAT_ID (see backend/.env.example). Silently disabled if unset.
"""

from __future__ import annotations

import json

import httpx

from app.config import get_settings
from app.db.models import ArbitrageOpportunity, Event, ParlayValueFind, ValueEdge

TELEGRAM_API_BASE = "https://api.telegram.org"


def format_pick_box(index: int, event: Event, opportunity: ArbitrageOpportunity) -> str:
    legs = json.loads(opportunity.legs_json)
    market_label = opportunity.market.replace("_", " ").title()
    line_part = f" (라인 {opportunity.line})" if opportunity.line is not None else ""
    legs_text = "\n".join(f"  ▸ {leg['selection']} @ {leg['bookmaker']}  {leg['decimal_odds']:.2f}" for leg in legs)
    push_note = "\n  ⚠️ 푸시 가능 — 최악의 경우 원금 보전" if opportunity.push_possible else ""
    return (
        f"[{index}] {event.home_team} vs {event.away_team}\n"
        f"마켓: {market_label}{line_part}\n"
        f"확정 마진: +{opportunity.margin_percent:.2f}%\n"
        f"{legs_text}{push_note}"
    )


def format_digest(boxes: list[str]) -> str:
    header = f"🎯 확정 수익 픽 {len(boxes)}건 발견\n"
    return header + "\n\n".join(boxes)


def format_value_edge_box(index: int, event: Event, edge: ValueEdge) -> str:
    market_label = edge.market.replace("_", " ").title()
    line_part = f" (라인 {edge.line})" if edge.line is not None else ""
    return (
        f"[{index}] {event.home_team} vs {event.away_team}\n"
        f"마켓: {market_label}{line_part} · 선택: {edge.selection}\n"
        f"{edge.bookmaker} @ {edge.quoted_decimal_odds:.2f}  (모델 엣지 +{edge.edge_percent:.1f}%)"
    )


def format_value_edge_digest(boxes: list[str]) -> str:
    header = f"🔎 가치 베팅 엣지 {len(boxes)}건 발견 (확정 수익 아님)\n"
    return header + "\n\n".join(boxes)


def format_parlay_box(index: int, find: ParlayValueFind) -> str:
    legs = json.loads(find.legs_json)
    legs_text = "\n".join(
        f"  ▸ {leg['event_label']}: {leg['selection']} @ {leg['bookmaker']}  {leg['decimal_odds']:.2f}" for leg in legs
    )
    return (
        f"[{index}] {find.bookmaker} · {len(legs)}다리 다폴더\n"
        f"{legs_text}\n"
        f"합산 배당 {find.combined_odds:.2f}  (모델 엣지 +{find.edge_percent:.1f}%)"
    )


def format_parlay_digest(boxes: list[str]) -> str:
    header = f"🧩 다폴더 가치 픽 {len(boxes)}건 발견 (확정 수익 아님)\n"
    return header + "\n\n".join(boxes)


async def send_telegram_message(text: str) -> bool:
    """Returns True if a send was attempted and didn't error, False if the
    channel isn't configured or the request failed (never raises — a
    notification failure must never break the scan cycle)."""
    settings = get_settings()
    if not settings.telegram_configured:
        return False
    url = f"{TELEGRAM_API_BASE}/bot{settings.telegram_bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                json={"chat_id": settings.telegram_chat_id, "text": text, "disable_web_page_preview": True},
            )
            resp.raise_for_status()
        return True
    except httpx.HTTPError:
        return False
