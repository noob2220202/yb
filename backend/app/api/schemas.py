from datetime import datetime

from pydantic import BaseModel, ConfigDict


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


class ParlayLegOut(BaseModel):
    event_label: str
    market: str
    line: float | None
    selection: str
    bookmaker: str
    decimal_odds: float
    fair_probability: float


class ParlayValueFindOut(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    id: int
    bookmaker: str
    combined_odds: float
    combined_fair_probability: float
    edge_percent: float
    legs: list[ParlayLegOut]
    detected_at: datetime
