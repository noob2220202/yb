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
    unverified in the response (see ``ScanGroupResult.verified``)."""

    market: str
    line: float | None = None
    selection: str
    bookmaker: str = ""
    decimal_odds: float = Field(gt=1.0)


class ScanRequest(BaseModel):
    total_stake: float = Field(gt=0)
    legs: list[ScanLegIn]


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


