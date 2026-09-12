import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_api_key
from app.api.schemas import (
    LegOut,
    OpportunityOut,
    StakeLegOut,
    StakePlanOut,
    ValueEdgeOut,
)
from app.core.enums import MarketType
from app.db.models import ArbitrageOpportunity, Event, ValueEdge
from app.db.session import get_session
from app.engine.arbitrage import ArbitrageResult, Leg, allocate_stakes
from app.scheduler import poll_and_scan

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/opportunities", response_model=list[OpportunityOut], dependencies=[Depends(require_api_key)])
async def list_opportunities(
    min_margin: float = Query(0.0, ge=0.0),
    limit: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[OpportunityOut]:
    stmt = (
        select(ArbitrageOpportunity, Event)
        .join(Event, ArbitrageOpportunity.event_id == Event.id)
        .where(ArbitrageOpportunity.margin_percent >= min_margin)
        .order_by(ArbitrageOpportunity.detected_at.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    out = []
    for opp, event in rows:
        legs = [LegOut(**leg) for leg in json.loads(opp.legs_json)]
        out.append(
            OpportunityOut(
                id=opp.id,
                event=f"{event.home_team} vs {event.away_team}",
                sport=event.sport,
                league=event.league,
                commence_time=event.commence_time,
                market=opp.market,
                line=opp.line,
                total_implied_probability=opp.total_implied_probability,
                margin_percent=opp.margin_percent,
                push_possible=opp.push_possible,
                legs=legs,
                detected_at=opp.detected_at,
            )
        )
    return out


@router.get(
    "/opportunities/{opportunity_id}/stake-plan",
    response_model=StakePlanOut,
    dependencies=[Depends(require_api_key)],
)
async def stake_plan(
    opportunity_id: int,
    total_stake: float = Query(..., gt=0),
    session: AsyncSession = Depends(get_session),
) -> StakePlanOut:
    opp = await session.get(ArbitrageOpportunity, opportunity_id)
    if opp is None:
        raise HTTPException(status_code=404, detail="opportunity not found")

    try:
        legs = tuple(
            Leg(selection=leg["selection"], bookmaker=leg["bookmaker"], decimal_odds=leg["decimal_odds"])
            for leg in json.loads(opp.legs_json)
        )
        result = ArbitrageResult(
            market=MarketType(opp.market),
            line=opp.line,
            legs=legs,
            total_implied_probability=opp.total_implied_probability,
            push_possible=opp.push_possible,
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        # legs_json/market are always written by our own scanner, so this
        # should never happen — but a stored record failing to reconstruct
        # should surface as a clear 500, not an unhandled crash.
        raise HTTPException(status_code=500, detail="stored opportunity record is corrupted") from exc

    try:
        plan = allocate_stakes(result, total_stake)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return StakePlanOut(
        total_stake=plan.total_stake,
        guaranteed_profit=plan.guaranteed_profit,
        profit_percent=plan.profit_percent,
        push_possible=plan.push_possible,
        legs=[StakeLegOut(**leg.__dict__) for leg in plan.legs],
    )


@router.get("/value-edges", response_model=list[ValueEdgeOut], dependencies=[Depends(require_api_key)])
async def list_value_edges(
    min_edge: float = Query(2.0, ge=0.0),
    limit: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[ValueEdgeOut]:
    stmt = (
        select(ValueEdge, Event)
        .join(Event, ValueEdge.event_id == Event.id)
        .where(ValueEdge.edge_percent >= min_edge)
        .order_by(ValueEdge.detected_at.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [
        ValueEdgeOut(
            id=edge.id,
            event=f"{event.home_team} vs {event.away_team}",
            sport=event.sport,
            market=edge.market,
            line=edge.line,
            selection=edge.selection,
            bookmaker=edge.bookmaker,
            quoted_decimal_odds=edge.quoted_decimal_odds,
            model_probability=edge.model_probability,
            implied_probability=edge.implied_probability,
            edge_percent=edge.edge_percent,
            detected_at=edge.detected_at,
        )
        for edge, event in rows
    ]


@router.post("/admin/poll", dependencies=[Depends(require_api_key)])
async def trigger_poll() -> dict:
    opportunities, edges = await poll_and_scan()
    return {"opportunities_found": len(opportunities), "value_edges_found": len(edges)}
