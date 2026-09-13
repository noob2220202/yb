from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LegOut(BaseModel):
    selection: str
    bookmaker: str
    decimal_odds: float


class OpportunityOut(BaseModel):
    id: int
    event: str
    sport: str
    league: str
    commence_time: datetime
    market: str
    line: float | None
    total_implied_probability: float
    margin_percent: float
    push_possible: bool
    legs: list[LegOut]
    detected_at: datetime


class StakeLegOut(BaseModel):
    selection: str
    bookmaker: str
    decimal_odds: float
    stake: float
    payout: float


class StakePlanOut(BaseModel):
    total_stake: float
    guaranteed_profit: float
    profit_percent: float
    push_possible: bool
    legs: list[StakeLegOut]


class ScanLegIn(BaseModel):
    """One manually-entered price. ``market`` is either one of this
    project's known clean-partition ``MarketType`` values (moneyline_3way,
    moneyline_2way, european_handicap, totals, asian_handicap, btts) --
    which get the fully engine-verified treatment, quarter lines included
    -- or ANY other string (``correct_score``, a custom label like "코너킥
    오버 9.5", ...), which is grouped and summed but explicitly marked
    unverified in the response (see ``ScanGroupResult.verified``).

    ``group`` optionally overrides which other legs this one gets pooled
    with for the arbitrage check -- default grouping is by (market, line),
    so give two legs from otherwise-different markets the same ``group``
    string to force them into one combined calculation (e.g. mixing a
    moneyline leg with a totals leg). Always unverified when it mixes
    different market types -- see the endpoint docstring for why."""

    market: str
    line: float | None = None
    selection: str
    bookmaker: str = ""
    decimal_odds: float = Field(gt=1.0)
    group: str | None = None


class ScanRequest(BaseModel):
    total_stake: float = Field(gt=0)
    legs: list[ScanLegIn]
    # Optional: also look for a partial-coverage pick per eligible market
    # group -- betting only SOME of a market's outcomes (dropping the
    # ones you judge least likely) raises the margin at the cost of a
    # real chance of losing the whole stake if a dropped outcome hits.
    # See ScanResponse.hit_rate_picks / HitRatePickResult.
    min_hit_rate_percent: float | None = Field(default=None, ge=0.0, le=100.0)


class ScanGroupResult(BaseModel):
    market: str
    market_label: str
    line: float | None
    verified: bool
    is_arbitrage: bool
    total_implied_probability: float
    margin_percent: float
    push_possible: bool
    quarter_line: bool
    guaranteed_profit: float
    profit_percent: float
    legs: list[StakeLegOut]
    warning: str | None = None


class HitRatePickResult(BaseModel):
    """A deliberately partial hedge: bet only on a SUBSET of one market's
    outcomes (never all of them -- that's just ``ScanGroupResult``
    again), chosen to maximize margin subject to the subset's combined
    fair (devigged) probability meeting ``target_hit_rate_percent``. If
    the actual result falls in ``excluded_selections`` instead, the whole
    stake is lost -- this is NOT a guarantee, just the best margin
    available at that risk level. Only computed for markets whose full
    outcome set is known (the same clean-partition markets that can be
    ``verified``) -- an open-ended market like correct score has no
    reliable "everything else" probability to subtract, so it's never
    considered here.
    """

    market: str
    market_label: str
    line: float | None
    target_hit_rate_percent: float
    achieved_hit_rate_percent: float
    margin_percent: float
    guaranteed_profit: float
    profit_percent: float
    excluded_selections: list[str]
    legs: list[StakeLegOut]


class ScanResponse(BaseModel):
    groups: list[ScanGroupResult]
    hit_rate_picks: list[HitRatePickResult]


class ValueEdgeOut(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    id: int
    event: str
    sport: str
    market: str
    line: float | None
    selection: str
    bookmaker: str
    quoted_decimal_odds: float
    model_probability: float
    implied_probability: float
    edge_percent: float
    detected_at: datetime


