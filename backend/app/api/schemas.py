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


class ManualLegIn(BaseModel):
    selection: str
    bookmaker: str = ""
    decimal_odds: float = Field(gt=1.0)


class ManualCalculationIn(BaseModel):
    market: str
    line: float | None = None
    total_stake: float = Field(gt=0)
    legs: list[ManualLegIn]


class ManualCalculationOut(BaseModel):
    is_arbitrage: bool
    total_implied_probability: float
    margin_percent: float
    push_possible: bool
    quarter_line: bool
    total_stake: float
    guaranteed_profit: float
    profit_percent: float
    legs: list[StakeLegOut]


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


