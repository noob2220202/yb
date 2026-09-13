from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_api_key
from app.api.schemas import (
    HedgeBoxCalculateRequest,
    HedgeBoxCalculateResponse,
    HedgeBoxDetailOut,
    HedgeBoxLegIn,
    HedgeBoxSendRequest,
    HedgeBoxSendResponse,
    MatchOut,
    MatchSelectionOut,
)
from app.config import get_settings
from app.core.enums import MarketType
from app.db.models import SentHedgeBox
from app.db.session import get_session
from app.engine.hedgebox import HedgeBoxError, calculate_hedge_box
from app.engine.scanner import get_latest_quotes_by_event, get_upcoming_events
from app.notifications.telegram import build_hedge_box_keyboard, format_hedge_box, send_telegram_message

router = APIRouter()

_MARKET_LABELS: dict[MarketType, str] = {
    MarketType.MONEYLINE_3WAY: "승무패",
    MarketType.MONEYLINE_2WAY: "승패",
    MarketType.EUROPEAN_HANDICAP: "유럽식 핸디캡",
    MarketType.TOTALS: "오버언더",
    MarketType.ASIAN_HANDICAP: "아시안 핸디캡",
    MarketType.BOTH_TEAMS_TO_SCORE: "양팀득점",
    MarketType.CORRECT_SCORE: "정확한 스코어",
    MarketType.WINNING_MARGIN: "몇점차승리",
}

_SELECTION_LABELS: dict[str, str] = {
    "yes": "예",
    "no": "아니오",
    "over": "오버",
    "under": "언더",
    "draw": "무승부",
}


def _fmt_handicap(line: float) -> str:
    text = f"{line:+.2f}".rstrip("0").rstrip(".")
    return text if text not in ("+", "-") else "+0"


def _market_label(market_str: str) -> str:
    try:
        return _MARKET_LABELS.get(MarketType(market_str), market_str)
    except ValueError:
        return market_str


def _selection_label(market_str: str, selection: str, line: float | None, home_team: str, away_team: str) -> str:
    try:
        market = MarketType(market_str)
    except ValueError:
        market = None

    if market in (MarketType.ASIAN_HANDICAP, MarketType.EUROPEAN_HANDICAP):
        if selection == "home" and line is not None:
            return f"{home_team} {_fmt_handicap(line)}"
        if selection == "away" and line is not None:
            return f"{away_team} {_fmt_handicap(-line)}"
        if selection == "draw":
            return "무승부"
    if market == MarketType.TOTALS and line is not None:
        return f"{_SELECTION_LABELS.get(selection, selection)} {line}"
    if selection == "home":
        return home_team
    if selection == "away":
        return away_team
    return _SELECTION_LABELS.get(selection, selection)


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/matches", response_model=list[MatchOut], dependencies=[Depends(require_api_key)])
async def list_matches(
    hours_ahead: float = Query(72.0, gt=0, le=24 * 14),
    sport: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> list[MatchOut]:
    """Upcoming matches with the latest auto-fetched odds per selection --
    a starting point for the hedge box builder. The admin always confirms
    or overrides these prices before calculating a box; nothing here is
    ever bet automatically."""
    events = await get_upcoming_events(session, hours_ahead=hours_ahead, sport=sport)
    quotes_by_event = await get_latest_quotes_by_event(session, [e.id for e in events])

    out = []
    for event in events:
        quotes = quotes_by_event.get(event.id, [])
        selections = [
            MatchSelectionOut(
                market=q.market,
                market_label=_market_label(q.market),
                line=q.line,
                selection=q.selection,
                selection_label=_selection_label(q.market, q.selection, q.line, event.home_team, event.away_team),
                decimal_odds=q.decimal_odds,
                bookmaker=q.bookmaker,
                fetched_at=q.fetched_at,
            )
            for q in quotes
        ]
        if not selections:
            continue
        out.append(
            MatchOut(
                id=event.id,
                event=f"{event.home_team} vs {event.away_team}",
                sport=event.sport,
                league=event.league,
                commence_time=event.commence_time,
                selections=selections,
            )
        )
    return out


def _calculate_or_400(leg_a: HedgeBoxLegIn, leg_b: HedgeBoxLegIn, target_profit: float, stake_round_to: float, excluded_odds: float | None):
    try:
        return calculate_hedge_box(
            odds_a=leg_a.decimal_odds,
            odds_b=leg_b.decimal_odds,
            target_profit=target_profit,
            stake_round_to=stake_round_to,
            excluded_odds=excluded_odds,
        )
    except HedgeBoxError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/hedge-box/calculate",
    response_model=HedgeBoxCalculateResponse,
    dependencies=[Depends(require_api_key)],
)
async def calculate_box(payload: HedgeBoxCalculateRequest) -> HedgeBoxCalculateResponse:
    """Given two odds you plan to actually bet and a target net profit
    (KRW), back-solves the stake on each leg so that whichever hits, the
    profit comes out to ~that target. Never a guaranteed-profit signal —
    see app/engine/hedgebox.py module docstring."""
    result = _calculate_or_400(payload.leg_a, payload.leg_b, payload.target_profit, payload.stake_round_to, payload.excluded_odds)
    return HedgeBoxCalculateResponse(
        leg_a_stake=result.leg_a_stake,
        leg_b_stake=result.leg_b_stake,
        total_stake=result.total_stake,
        leg_a_payout=result.leg_a_payout,
        leg_b_payout=result.leg_b_payout,
        guaranteed_profit=result.guaranteed_profit,
        profit_percent=result.profit_percent,
        implied_hit_rate_percent=result.implied_hit_rate_percent,
    )


@router.post(
    "/hedge-box/send",
    response_model=HedgeBoxSendResponse,
    dependencies=[Depends(require_api_key)],
)
async def send_box(payload: HedgeBoxSendRequest, session: AsyncSession = Depends(get_session)) -> HedgeBoxSendResponse:
    """Recomputes the box server-side, logs it (for box numbering + the
    public detail page a box's Telegram button links to), then sends it
    to the configured Telegram channel with an inline "박스 상세보기"
    button."""
    result = _calculate_or_400(payload.leg_a, payload.leg_b, payload.target_profit, payload.stake_round_to, payload.excluded_odds)

    record = SentHedgeBox(
        event=payload.event,
        league=payload.league,
        commence_time=payload.commence_time,
        leg_a_label=payload.leg_a.label,
        leg_a_odds=payload.leg_a.decimal_odds,
        leg_a_stake=result.leg_a_stake,
        leg_b_label=payload.leg_b.label,
        leg_b_odds=payload.leg_b.decimal_odds,
        leg_b_stake=result.leg_b_stake,
        total_stake=result.total_stake,
        guaranteed_profit=result.guaranteed_profit,
        profit_percent=result.profit_percent,
        implied_hit_rate_percent=result.implied_hit_rate_percent,
        message_text="",
    )
    session.add(record)
    await session.flush()

    settings = get_settings()
    detail_url = f"{settings.public_frontend_url.rstrip('/')}/box/{record.id}"
    guide_url = f"{settings.public_frontend_url.rstrip('/')}/guide"

    message = format_hedge_box(
        box_id=record.id,
        event=payload.event,
        league=payload.league,
        commence_time=payload.commence_time,
        leg_a_label=payload.leg_a.label,
        leg_a_odds=payload.leg_a.decimal_odds,
        leg_a_stake=result.leg_a_stake,
        leg_b_label=payload.leg_b.label,
        leg_b_odds=payload.leg_b.decimal_odds,
        leg_b_stake=result.leg_b_stake,
        total_stake=result.total_stake,
        guaranteed_profit=result.guaranteed_profit,
        profit_percent=result.profit_percent,
        implied_hit_rate_percent=result.implied_hit_rate_percent,
    )
    record.message_text = message
    await session.commit()

    sent = await send_telegram_message(message, reply_markup=build_hedge_box_keyboard(detail_url, guide_url))

    return HedgeBoxSendResponse(
        box_id=record.id,
        sent=sent,
        detail_url=detail_url,
        message=message,
    )


@router.get("/hedge-box/{box_id}", response_model=HedgeBoxDetailOut)
async def get_box(box_id: int, session: AsyncSession = Depends(get_session)) -> HedgeBoxDetailOut:
    """Public (no API key) — this is what a Telegram channel member's
    "박스 상세보기" button opens, so it must be reachable without the
    admin's key."""
    record = await session.get(SentHedgeBox, box_id)
    if record is None:
        raise HTTPException(status_code=404, detail="박스를 찾을 수 없습니다.")
    return HedgeBoxDetailOut(
        id=record.id,
        created_at=record.created_at,
        event=record.event,
        league=record.league,
        commence_time=record.commence_time,
        leg_a_label=record.leg_a_label,
        leg_a_odds=record.leg_a_odds,
        leg_a_stake=record.leg_a_stake,
        leg_b_label=record.leg_b_label,
        leg_b_odds=record.leg_b_odds,
        leg_b_stake=record.leg_b_stake,
        total_stake=record.total_stake,
        guaranteed_profit=record.guaranteed_profit,
        profit_percent=record.profit_percent,
        implied_hit_rate_percent=record.implied_hit_rate_percent,
    )
