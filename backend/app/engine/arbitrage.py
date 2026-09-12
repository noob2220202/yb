"""Core arbitrage (surebet) math.

An arbitrage exists when the sum of the reciprocals of the best available
decimal odds for every outcome of a market ("total implied probability")
is below 1.0. Betting a stake on every outcome, sized proportionally to
1/odds, then guarantees the same profit no matter which outcome happens.

This module only accepts markets where the set of quoted selections forms
a genuine partition of what can happen (see ``expected_selections`` and
``is_supported_line``). Two subtleties matter for correctness:

1. **Push risk.** A Totals/Asian-Handicap line on a whole number (e.g.
   Over/Under 2.0) can push (stake refunded) if the final result lands
   exactly on the line. A push refunds every stake placed on that market,
   so the worst case is a breakeven, never a loss — the same
   sum(1/odds) < 1 condition still guarantees profit whenever the market
   *doesn't* push. ``ArbitrageResult.push_possible`` flags this so callers
   present it honestly ("guaranteed profit, or breakeven on a push") rather
   than as an unconditional win.

2. **Quarter lines** (e.g. -0.25, -0.75) split a stake across two
   half-lines with independent win/push outcomes each, which this simple
   model cannot represent correctly — those are excluded outright
   (``is_supported_line`` returns False) rather than silently mis-priced.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.core.enums import MarketType
from app.core.schemas import OddsQuote

_EXPECTED_SELECTIONS: dict[MarketType, frozenset[str]] = {
    MarketType.MONEYLINE_3WAY: frozenset({"home", "draw", "away"}),
    MarketType.MONEYLINE_2WAY: frozenset({"home", "away"}),
    MarketType.BOTH_TEAMS_TO_SCORE: frozenset({"yes", "no"}),
    MarketType.TOTALS: frozenset({"over", "under"}),
    MarketType.ASIAN_HANDICAP: frozenset({"home", "away"}),
}


def expected_selections(market: MarketType) -> frozenset[str]:
    return _EXPECTED_SELECTIONS.get(market, frozenset())


def _line_fraction(line: float) -> float:
    return round(abs(line) % 1, 2)


def is_supported_line(market: MarketType, line: float | None) -> bool:
    """Whether this engine can safely price this market/line combination."""
    if market not in (MarketType.TOTALS, MarketType.ASIAN_HANDICAP):
        return True
    if line is None:
        return False
    return _line_fraction(line) in (0.0, 0.5)


def push_possible(market: MarketType, line: float | None) -> bool:
    if market not in (MarketType.TOTALS, MarketType.ASIAN_HANDICAP):
        return False
    if line is None:
        return False
    return _line_fraction(line) == 0.0


@dataclass(frozen=True)
class Leg:
    selection: str
    bookmaker: str
    decimal_odds: float


@dataclass(frozen=True)
class ArbitrageResult:
    market: MarketType
    line: float | None
    legs: tuple[Leg, ...]
    total_implied_probability: float
    push_possible: bool

    @property
    def is_arbitrage(self) -> bool:
        return self.total_implied_probability < 1.0

    @property
    def margin_percent(self) -> float:
        """Guaranteed return on total stake, as a percentage."""
        return (1.0 / self.total_implied_probability - 1.0) * 100.0


def find_arbitrage(quotes: Iterable[OddsQuote]) -> ArbitrageResult | None:
    """Look for an arbitrage within one (event, market, line) group of quotes.

    ``quotes`` may come from several bookmakers; only the best (highest)
    odds per selection are used. Returns ``None`` when the line isn't one
    this engine can safely price, or when not every required outcome has a
    quote (an incomplete partition can't be arbed).
    """
    quotes = list(quotes)
    if not quotes:
        return None

    market = quotes[0].market
    line = quotes[0].line
    if any(q.market != market or q.line != line for q in quotes):
        raise ValueError("find_arbitrage requires quotes from a single (market, line) group")

    if not is_supported_line(market, line):
        return None

    required = expected_selections(market)
    if not required:
        return None

    best: dict[str, OddsQuote] = {}
    for q in quotes:
        if q.selection not in required:
            continue
        current = best.get(q.selection)
        if current is None or q.decimal_odds > current.decimal_odds:
            best[q.selection] = q

    if set(best.keys()) != set(required):
        return None

    total_implied = sum(1.0 / q.decimal_odds for q in best.values())
    legs = tuple(
        Leg(selection=q.selection, bookmaker=q.bookmaker, decimal_odds=q.decimal_odds)
        for q in best.values()
    )
    return ArbitrageResult(
        market=market,
        line=line,
        legs=legs,
        total_implied_probability=total_implied,
        push_possible=push_possible(market, line),
    )


@dataclass(frozen=True)
class StakeLeg:
    selection: str
    bookmaker: str
    decimal_odds: float
    stake: float
    payout: float


@dataclass(frozen=True)
class StakePlan:
    legs: tuple[StakeLeg, ...]
    total_stake: float
    guaranteed_profit: float
    profit_percent: float
    push_possible: bool


def allocate_stakes(result: ArbitrageResult, total_stake: float) -> StakePlan:
    """Split ``total_stake`` across every leg so every outcome pays out the
    same guaranteed profit.
    """
    if total_stake <= 0:
        raise ValueError("total_stake must be positive")
    if not result.is_arbitrage:
        raise ValueError("not an arbitrage: total implied probability >= 1.0")

    s = result.total_implied_probability
    legs = []
    for leg in result.legs:
        stake = total_stake * (1.0 / leg.decimal_odds) / s
        payout = stake * leg.decimal_odds
        legs.append(
            StakeLeg(
                selection=leg.selection,
                bookmaker=leg.bookmaker,
                decimal_odds=leg.decimal_odds,
                stake=round(stake, 2),
                payout=round(payout, 2),
            )
        )
    guaranteed_profit = total_stake * (1.0 / s - 1.0)
    return StakePlan(
        legs=tuple(legs),
        total_stake=total_stake,
        guaranteed_profit=round(guaranteed_profit, 2),
        profit_percent=(1.0 / s - 1.0) * 100.0,
        push_possible=result.push_possible,
    )
