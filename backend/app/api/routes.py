import itertools
import json
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import require_api_key
from app.api.schemas import (
    HitRatePickResult,
    LegOut,
    OpportunityOut,
    ScanGroupResult,
    ScanRequest,
    ScanResponse,
    StakeLegOut,
    StakePlanOut,
    ValueEdgeOut,
)
from app.core.enums import MarketType, Sport
from app.core.schemas import NormalizedEvent, OddsQuote
from app.db.models import ArbitrageOpportunity, Event, ValueEdge
from app.db.session import get_session
from app.engine.arbitrage import (
    ArbitrageResult,
    Leg,
    allocate_stakes,
    expected_selections,
    find_arbitrage,
    is_quarter_line,
    is_supported_line,
)
from app.scheduler import poll_and_scan

router = APIRouter()

# Placeholder event for the manual calculator below — find_arbitrage never
# reads event fields, it only needs every quote's (market, line) to match,
# so this exists purely to satisfy OddsQuote's required ``event`` field.
_CALCULATOR_EVENT = NormalizedEvent(
    sport=Sport.SOCCER, home_team="A", away_team="B", commence_time=datetime(2000, 1, 1, tzinfo=timezone.utc)
)

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


def _market_label(market_str: str, line: float | None) -> str:
    try:
        label = _MARKET_LABELS.get(MarketType(market_str), market_str)
    except ValueError:
        label = market_str
    return f"{label} ({line})" if line is not None else label


def _devig_proportional(odds_by_selection: dict[str, float]) -> dict[str, float]:
    """Proportional (multiplicative) devig: turns each selection's best
    available decimal odds into a no-vig fair probability, summing to 1
    across the FULL set passed in. Only meaningful when that full set is
    genuinely the complete, mutually-exclusive outcome space -- which is
    exactly why callers only use this for a verified clean-partition
    market group, never an open-ended one like correct score."""
    implied = {selection: 1.0 / odds for selection, odds in odds_by_selection.items()}
    total = sum(implied.values())
    return {selection: p / total for selection, p in implied.items()}


def _best_hit_rate_subset(
    odds_by_selection: dict[str, float],
    fair_by_selection: dict[str, float],
    target_hit_rate_percent: float,
) -> tuple[frozenset[str], float, float] | None:
    """Brute-forces every non-empty subset of selections (at most 2^3-1=7
    for this project's clean-partition markets, all 2- or 3-way) and
    returns the one with the lowest sum(1/odds) -- i.e. the highest
    margin -- among subsets whose combined fair probability clears the
    target. A smaller subset always has a lower (or equal) sum(1/odds)
    than a bigger one since every term is positive, so the full set
    (everything covered, hit rate 100%) is always the worst-margin
    candidate here -- that's the whole point of allowing a lower target.
    Returns (subset, achieved_hit_rate_fraction, total_implied_probability),
    or None if nothing clears the target.
    """
    selections = list(odds_by_selection.keys())
    target = target_hit_rate_percent / 100.0
    best: tuple[frozenset[str], float, float] | None = None
    for size in range(1, len(selections) + 1):
        for combo in itertools.combinations(selections, size):
            subset = frozenset(combo)
            hit_rate = sum(fair_by_selection[s] for s in subset)
            if hit_rate < target - 1e-9:
                continue
            total_implied = sum(1.0 / odds_by_selection[s] for s in subset)
            if best is None or total_implied < best[2]:
                best = (subset, hit_rate, total_implied)
    return best


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
            stake_fractions=json.loads(opp.stake_fractions_json) if opp.stake_fractions_json else None,
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


@router.post(
    "/calculator/scan",
    response_model=ScanResponse,
    dependencies=[Depends(require_api_key)],
)
async def scan_manual_odds(payload: ScanRequest) -> ScanResponse:
    """Manual what-if calculator — no scanner/DB involved. Takes a whole
    POOL of odds across as many markets as you want, all for ONE match
    (moneyline, European 3-way handicap, Asian handicap incl. quarter
    lines, totals, BTTS, correct score, or any custom label you type),
    groups them (by (market, line), or by an explicit ``group`` string a
    leg supplies to force it to be pooled with legs from a DIFFERENT
    market), and reports every group's margin so you can see which
    combination(s) among everything you entered actually clears 100%
    (``is_arbitrage``).

    Three tiers, always reported separately, never conflated:

    - ``verified=True``: a recognized clean-partition market with EXACTLY
      its required selections present. Priced by the same engine used for
      real detected opportunities — a genuine, checked guarantee when
      ``is_arbitrage`` is true.
    - ``verified=False`` (open-ended market): correct_score, a custom
      label, or a known market missing/duplicating a required selection.
      Still sums 1/odds and reports a margin, but it's only a TRUE
      guarantee if your own entries already cover every possible outcome
      — something this tool can't check for an open-ended market.
    - ``verified=False`` (mixed markets): a ``group`` was used to force
      legs from genuinely different markets together (e.g. a moneyline
      leg with a totals leg). These are almost never statistically
      independent (winning and total goals correlate), so multiplying/
      summing their raw odds together is NOT a valid arbitrage check —
      only real, honest if you priced the actual combined market a
      bookmaker sells (e.g. "Home win & Over 2.5" as its own single
      quote), never by combining two separate markets' odds yourself.

    Every case always carries an explanatory ``warning`` except the
    fully engine-verified one.

    If ``payload.min_hit_rate_percent`` is set, ALSO returns
    ``hit_rate_picks``: for every verified group, the best-margin subset
    of its outcomes whose combined devigged fair probability clears that
    target (see ``_best_hit_rate_subset``) — a deliberate partial hedge,
    never a guarantee, kept in its own list so it's never confused with
    the guaranteed ``groups`` results.
    """
    groups: dict[str, list] = defaultdict(list)
    for leg in payload.legs:
        key = (leg.group or "").strip() or f"{leg.market}:{leg.line}"
        groups[key].append(leg)

    results: list[ScanGroupResult] = []
    hit_rate_picks: list[HitRatePickResult] = []
    for key, group_legs in groups.items():
        market_values = {leg.market for leg in group_legs}
        line_values = {leg.line for leg in group_legs}
        uniform_market = len(market_values) == 1
        uniform_line = len(line_values) == 1
        market_str = next(iter(market_values)) if uniform_market else key
        line = next(iter(line_values)) if uniform_line else None

        market: MarketType | None = None
        if uniform_market:
            try:
                market = MarketType(market_str)
            except ValueError:
                market = None

        required = expected_selections(market) if market is not None else frozenset()
        got_selections = [leg.selection for leg in group_legs]
        is_verifiable = (
            uniform_market
            and uniform_line
            and market is not None
            and bool(required)
            and set(got_selections) == set(required)
            and len(got_selections) == len(required)
            and is_supported_line(market, line)
        )

        if is_verifiable:
            if payload.min_hit_rate_percent is not None:
                best_odds_map: dict[str, tuple[float, str]] = {}
                for i, leg in enumerate(group_legs):
                    current = best_odds_map.get(leg.selection)
                    if current is None or leg.decimal_odds > current[0]:
                        best_odds_map[leg.selection] = (leg.decimal_odds, leg.bookmaker or f"북메이커{i + 1}")
                fair = _devig_proportional({sel: odds for sel, (odds, _bk) in best_odds_map.items()})
                picked = _best_hit_rate_subset(
                    {sel: odds for sel, (odds, _bk) in best_odds_map.items()}, fair, payload.min_hit_rate_percent
                )
                if picked is not None:
                    subset, achieved_hit_rate, total_implied = picked
                    hit_margin_percent = (1.0 / total_implied - 1.0) * 100.0
                    hit_legs_out = []
                    for sel in subset:
                        odds, bookmaker = best_odds_map[sel]
                        fraction = (1.0 / odds) / total_implied
                        stake = payload.total_stake * fraction
                        hit_legs_out.append(
                            StakeLegOut(
                                selection=sel,
                                bookmaker=bookmaker,
                                decimal_odds=odds,
                                stake=round(stake, 2),
                                payout=round(stake * odds, 2),
                            )
                        )
                    hit_rate_picks.append(
                        HitRatePickResult(
                            market=market_str,
                            market_label=_market_label(market_str, line),
                            line=line,
                            target_hit_rate_percent=payload.min_hit_rate_percent,
                            achieved_hit_rate_percent=achieved_hit_rate * 100.0,
                            margin_percent=hit_margin_percent,
                            guaranteed_profit=round(payload.total_stake * (1.0 / total_implied - 1.0), 2),
                            profit_percent=hit_margin_percent,
                            excluded_selections=sorted(set(best_odds_map.keys()) - subset),
                            legs=hit_legs_out,
                        )
                    )

            quotes = [
                OddsQuote(
                    event=_CALCULATOR_EVENT,
                    bookmaker=leg.bookmaker or f"북메이커{i + 1}",
                    market=market,
                    selection=leg.selection,
                    decimal_odds=leg.decimal_odds,
                    line=line,
                )
                for i, leg in enumerate(group_legs)
            ]
            result = find_arbitrage(quotes)
            if result is not None:
                plan = allocate_stakes(result, payload.total_stake, allow_negative=True)
                results.append(
                    ScanGroupResult(
                        market=market_str,
                        market_label=_market_label(market_str, line),
                        line=line,
                        verified=True,
                        is_arbitrage=result.is_arbitrage,
                        total_implied_probability=result.total_implied_probability,
                        margin_percent=result.margin_percent,
                        push_possible=result.push_possible,
                        quarter_line=is_quarter_line(market, line),
                        guaranteed_profit=plan.guaranteed_profit,
                        profit_percent=plan.profit_percent,
                        legs=[StakeLegOut(**leg.__dict__) for leg in plan.legs],
                        warning=None,
                    )
                )
                continue

        # Unverified fallback: a custom/open-ended market, or a known
        # market missing/duplicating a required selection. Still useful,
        # never hidden -- just never claimed as a proven guarantee.
        if len(group_legs) < 2:
            continue  # nothing to arbitrage with a single price

        total_implied = sum(1.0 / leg.decimal_odds for leg in group_legs)
        margin_percent = (1.0 / total_implied - 1.0) * 100.0
        legs_out = []
        for i, leg in enumerate(group_legs):
            fraction = (1.0 / leg.decimal_odds) / total_implied
            stake = payload.total_stake * fraction
            legs_out.append(
                StakeLegOut(
                    selection=leg.selection,
                    bookmaker=leg.bookmaker or f"북메이커{i + 1}",
                    decimal_odds=leg.decimal_odds,
                    stake=round(stake, 2),
                    payout=round(stake * leg.decimal_odds, 2),
                )
            )
        guaranteed_profit = payload.total_stake * (1.0 / total_implied - 1.0)

        if not uniform_market:
            warning = (
                "서로 다른 마켓을 한 그룹으로 묶었습니다 — 승패·오버언더처럼 서로 다른 "
                "마켓의 결과는 대부분 통계적으로 독립이 아니라서(예: 이기는 팀과 총 득점은 "
                "서로 영향을 줌), 각각의 배당을 그대로 묶어서 계산한 이 숫자는 확정 수익이 "
                "아닙니다. 북메이커가 실제로 파는 결합 마켓(예: '홈팀 승리 & 오버 2.5')의 "
                "가격을 그 자체로 하나의 배당으로 넣었을 때만 이 숫자가 의미를 가집니다."
            )
        elif market is not None and required:
            warning = (
                f"이 마켓에는 다음 선택지가 정확히 하나씩 필요합니다: {sorted(required)} "
                f"(지금 입력: {sorted(set(got_selections))}) — 부족하거나 중복된 채로는 "
                "검증할 수 없어 단순 배당 합산만 보여드립니다."
            )
        else:
            warning = (
                "이 마켓은 엔진이 검증하지 않습니다 — 입력한 선택지가 실제로 일어날 수 있는 "
                "모든 경우의 수를 빠짐없이 포함해야만 진짜 확정 수익입니다. 정확한 스코어처럼 "
                "경우의 수가 사실상 무한한 마켓은 '기타 전체' 같은 캐치올 선택지 없이는 "
                "확정 수익이 될 수 없습니다."
            )

        results.append(
            ScanGroupResult(
                market=market_str,
                market_label=_market_label(market_str, line),
                line=line,
                verified=False,
                is_arbitrage=total_implied < 1.0,
                total_implied_probability=total_implied,
                margin_percent=margin_percent,
                push_possible=False,
                quarter_line=False,
                guaranteed_profit=round(guaranteed_profit, 2),
                profit_percent=margin_percent,
                legs=legs_out,
                warning=warning,
            )
        )

    results.sort(key=lambda r: (not r.verified, not r.is_arbitrage, -r.margin_percent))
    hit_rate_picks.sort(key=lambda p: -p.margin_percent)
    return ScanResponse(groups=results, hit_rate_picks=hit_rate_picks)


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
    return {
        "opportunities_found": len(opportunities),
        "value_edges_found": len(edges),
    }
