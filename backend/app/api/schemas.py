from datetime import datetime

from pydantic import BaseModel, Field


class MatchSelectionOut(BaseModel):
    """One currently-quoted price for a match, as fetched automatically
    from a provider -- shown as a suggested starting point in the hedge
    box builder; the admin can always override it with their own real
    bet-slip price before calculating."""

    market: str
    market_label: str
    line: float | None
    selection: str
    selection_label: str
    decimal_odds: float
    bookmaker: str
    fetched_at: datetime


class MatchOut(BaseModel):
    id: int
    event: str
    sport: str
    league: str
    commence_time: datetime
    selections: list[MatchSelectionOut]


class HedgeBoxLegIn(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    decimal_odds: float = Field(gt=1.0)


class HedgeBoxCalculateRequest(BaseModel):
    leg_a: HedgeBoxLegIn
    leg_b: HedgeBoxLegIn
    target_profit: float = Field(gt=0)
    stake_round_to: float = Field(default=100.0, gt=0)
    # Price of the outcome you're deliberately leaving uncovered, if you
    # know it -- used only to estimate a hit rate for your own sanity
    # check, never bet.
    excluded_odds: float | None = Field(default=None, gt=1.0)


class HedgeBoxCalculateResponse(BaseModel):
    leg_a_stake: float
    leg_b_stake: float
    total_stake: float
    leg_a_payout: float
    leg_b_payout: float
    guaranteed_profit: float
    profit_percent: float
    implied_hit_rate_percent: float | None


class HedgeBoxSendRequest(BaseModel):
    """Recomputes stakes/profit server-side from the same inputs as
    ``/hedge-box/calculate`` (never trusts client-sent numbers) before
    building and sending the Telegram message, so the two endpoints can
    never disagree."""

    event: str = Field(min_length=1, max_length=200)
    league: str = ""
    commence_time: datetime
    leg_a: HedgeBoxLegIn
    leg_b: HedgeBoxLegIn
    target_profit: float = Field(gt=0)
    stake_round_to: float = Field(default=100.0, gt=0)
    excluded_odds: float | None = Field(default=None, gt=1.0)


class HedgeBoxSendResponse(BaseModel):
    box_id: int
    sent: bool
    detail_url: str
    message: str


class HedgeBoxDetailOut(BaseModel):
    id: int
    created_at: datetime
    event: str
    league: str
    commence_time: datetime
    leg_a_label: str
    leg_a_odds: float
    leg_a_stake: float
    leg_b_label: str
    leg_b_odds: float
    leg_b_stake: float
    total_stake: float
    guaranteed_profit: float
    profit_percent: float
    implied_hit_rate_percent: float | None
