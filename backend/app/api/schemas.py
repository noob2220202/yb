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


class SystemBetLegIn(BaseModel):
    """One selection in a system bet. ``probability_percent`` is YOUR OWN
    estimate of its true win chance, deliberately separate from
    ``decimal_odds`` — deriving it from the same odds (1/odds) makes the
    expected-value math trivially collapse to breakeven no matter what
    the odds are (see app/engine/systembet.py module docstring), so leave
    it blank only if you understand the result will show ~0% expected
    value rather than a real edge."""

    label: str
    bookmaker: str = ""
    decimal_odds: float = Field(gt=1.0)
    probability_percent: float | None = Field(default=None, ge=0.0, le=100.0)


class SystemBetRequest(BaseModel):
    total_stake: float = Field(gt=0)
    min_hit_rate_percent: float = Field(ge=0.0, le=100.0)
    legs: list[SystemBetLegIn]


class SystemBetBreakdownItem(BaseModel):
    combo_size: int
    count: int


class SystemBetOut(BaseModel):
    num_selections: int
    min_hits: int
    achieved_hit_rate_percent: float
    num_bets: int
    unit_stake: float
    total_stake: float
    expected_profit: float
    expected_profit_percent: float
    best_case_profit: float
    best_case_profit_percent: float
    breakdown: list[SystemBetBreakdownItem]
    used_naive_probability: bool
    warning: str


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


