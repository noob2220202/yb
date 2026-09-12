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
   half-lines, each with its own independent win/push outcome. Unlike a
   whole-number line's single push, this has THREE possible settlements,
   not two:

   - decisive win (both halves win)
   - decisive loss (both halves lose)
   - a "marginal" outcome where one half pushes (refunds) and the other
     half either wins or loses on its own — a genuine partial win/loss,
     not a push of the whole stake.

   Concretely, decompose line ``L`` into its two half-step components
   ``c_lo = L - 0.25`` and ``c_hi = L + 0.25`` (both multiples of 0.5).
   Exactly one of them is a whole number (the one that can push); the
   other is a half-line (never pushes). Whichever fractional part ``L``
   has (``.25`` vs ``.75``, via floor-mod so sign is handled correctly)
   determines whether the home/over side's marginal outcome is a HALF WIN
   or a HALF LOSS — see ``_home_marginal_is_half_win``. This is exactly
   the well-known real-world behaviour of quarter Asian Handicap/Totals
   lines (e.g. -0.25 loses half the stake on a draw; -0.75 wins half the
   stake when the home side wins by exactly 1).

   Because the marginal bucket's payout isn't simply "refund" (unlike a
   whole-number push), the classic ``sum(1/odds) < 1`` shortcut doesn't
   apply directly — the stake split between the two sides has to be
   solved for the allocation that maximizes the WORST of the three
   bucket payouts (see ``_quarter_line_worst_case``), not the simple
   proportional-to-1/odds split used for clean partitions.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.core.enums import MarketType
from app.core.schemas import OddsQuote

_EXPECTED_SELECTIONS: dict[MarketType, frozenset[str]] = {
    MarketType.MONEYLINE_3WAY: frozenset({"home", "draw", "away"}),
    MarketType.MONEYLINE_2WAY: frozenset({"home", "away"}),
    MarketType.EUROPEAN_HANDICAP: frozenset({"home", "draw", "away"}),
    MarketType.BOTH_TEAMS_TO_SCORE: frozenset({"yes", "no"}),
    MarketType.TOTALS: frozenset({"over", "under"}),
    MarketType.ASIAN_HANDICAP: frozenset({"home", "away"}),
}


def expected_selections(market: MarketType) -> frozenset[str]:
    return _EXPECTED_SELECTIONS.get(market, frozenset())


def _line_fraction(line: float) -> float:
    """Absolute fractional part — fine for checking *whether* a line is a
    whole/half/quarter number, since 0.0/0.5 and .25/.75 are each
    symmetric under sign. Do NOT use this to determine quarter-line
    *flavor* (half-win vs half-loss) — that needs the sign-aware
    ``_signed_line_fraction`` below.
    """
    return round(abs(line) % 1, 2)


def _signed_line_fraction(line: float) -> float:
    """Floor-mod fractional part, always in [0, 1) — distinguishes e.g.
    -0.25 (0.75) from +0.25 (0.25), which matters for quarter-line
    settlement (see module docstring)."""
    return round(line % 1.0, 2)


def is_supported_line(market: MarketType, line: float | None) -> bool:
    """Whether this engine can safely price this market/line combination."""
    if market not in (MarketType.TOTALS, MarketType.ASIAN_HANDICAP):
        return True
    if line is None:
        return False
    return _line_fraction(line) in (0.0, 0.25, 0.5, 0.75)


def is_quarter_line(market: MarketType, line: float | None) -> bool:
    if market not in (MarketType.TOTALS, MarketType.ASIAN_HANDICAP):
        return False
    if line is None:
        return False
    return _line_fraction(line) in (0.25, 0.75)


def push_possible(market: MarketType, line: float | None) -> bool:
    """Whole-stake push risk — only whole-number lines. Quarter lines have
    a different (partial) marginal outcome, already fully priced into
    ``ArbitrageResult.margin_percent`` for them (see
    ``_quarter_line_worst_case``), so this is correctly False for them:
    there's no separate caveat left to flag."""
    if market not in (MarketType.TOTALS, MarketType.ASIAN_HANDICAP):
        return False
    if line is None:
        return False
    return _line_fraction(line) == 0.0


def _home_marginal_is_half_win(line: float) -> bool:
    """Whether the home/over side's marginal (one-half-pushes) outcome on
    a quarter line is a half WIN (vs half LOSS) — see module docstring
    for the derivation. E.g. home -0.25 half-loses on a draw (False here);
    home -0.75 half-wins when winning by exactly 1 (True here)."""
    return _signed_line_fraction(line) == 0.25


def _quarter_line_worst_case(
    odds_a: float, odds_b: float, a_marginal_is_half_win: bool
) -> tuple[float, float]:
    """For a quarter-line market's two sides (``a`` = home/over, ``b`` =
    away/under, same line, unit total stake split as ``s_a`` / ``1 -
    s_a``), returns ``(best_s_a, worst_case_profit_fraction)`` — the
    stake split that maximizes the worst of the three possible bucket
    payouts, and that worst-case profit as a fraction of stake.

    The three payout curves (decisive-a-win, marginal, decisive-b-win) are
    each affine in ``s_a``, so the max-min is found either at a boundary
    (``s_a`` in {0, 1}) or where two of the curves cross — no numerical
    optimizer needed, just evaluate every candidate crossing.
    """

    def payouts(s_a: float) -> tuple[float, float, float]:
        s_b = 1.0 - s_a
        decisive_a = s_a * odds_a
        decisive_b = s_b * odds_b
        if a_marginal_is_half_win:
            marginal = 0.5 * s_a * (odds_a + 1.0) + 0.5 * s_b
        else:
            marginal = 0.5 * s_a + 0.5 * s_b * (odds_b + 1.0)
        return decisive_a, marginal, decisive_b

    # decisive_a(s) = odds_a * s
    # decisive_b(s) = odds_b - odds_b * s
    # marginal(s)   = m_slope * s + m_intercept
    if a_marginal_is_half_win:
        m_slope = 0.5 * (odds_a + 1.0) - 0.5
        m_intercept = 0.5
    else:
        m_slope = 0.5 - 0.5 * (odds_b + 1.0)
        m_intercept = 0.5 * (odds_b + 1.0)

    def intersection(slope1: float, intercept1: float, slope2: float, intercept2: float) -> float | None:
        if abs(slope1 - slope2) < 1e-12:
            return None
        s = (intercept2 - intercept1) / (slope1 - slope2)
        return s if 0.0 <= s <= 1.0 else None

    candidates = {0.0, 1.0}
    for s in (
        intersection(odds_a, 0.0, -odds_b, odds_b),
        intersection(odds_a, 0.0, m_slope, m_intercept),
        intersection(-odds_b, odds_b, m_slope, m_intercept),
    ):
        if s is not None:
            candidates.add(s)

    best_s = 0.0
    best_worst_case = float("-inf")
    for s in candidates:
        worst = min(payouts(s))
        if worst > best_worst_case:
            best_worst_case = worst
            best_s = s
    return best_s, best_worst_case - 1.0


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
    # Only set for quarter-line results: {selection: stake_fraction},
    # summing to 1.0. The naive 1/odds-proportional split (used when this
    # is None) doesn't account for the marginal bucket's partial-win/loss
    # payout, so quarter lines need their own solved-for split — see
    # ``_quarter_line_worst_case``. Persisted alongside the opportunity
    # (``ArbitrageOpportunity.stake_fractions_json``) so a later
    # stake-plan request can reconstruct it exactly.
    stake_fractions: dict[str, float] | None = None

    @property
    def is_arbitrage(self) -> bool:
        return self.total_implied_probability < 1.0

    @property
    def margin_percent(self) -> float:
        """Guaranteed return on total stake, as a percentage."""
        return (1.0 / self.total_implied_probability - 1.0) * 100.0


def best_odds_per_selection(quotes: Iterable[OddsQuote], required: frozenset[str]) -> dict[str, OddsQuote] | None:
    """Picks the best (highest) odds per selection across every quote in
    ``quotes`` for one (event, market, line) group. Returns ``None`` if
    any of ``required`` has no quote at all (an incomplete partition).
    """
    best: dict[str, OddsQuote] = {}
    for q in quotes:
        if q.selection not in required:
            continue
        current = best.get(q.selection)
        if current is None or q.decimal_odds > current.decimal_odds:
            best[q.selection] = q

    if set(best.keys()) != set(required):
        return None
    return best


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

    best = best_odds_per_selection(quotes, required)
    if best is None:
        return None

    if is_quarter_line(market, line):
        return _find_quarter_line_arbitrage(market, line, best)

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


def _find_quarter_line_arbitrage(
    market: MarketType, line: float, best: dict[str, OddsQuote]
) -> ArbitrageResult | None:
    """Quarter-line (.25/.75) counterpart to the plain-partition path
    above — see module docstring for the settlement model. ``best`` must
    have exactly the two sides of a two-way market (home/away or
    over/under, whichever ``market`` expects)."""
    side_a, side_b = ("home", "away") if market == MarketType.ASIAN_HANDICAP else ("over", "under")
    quote_a, quote_b = best.get(side_a), best.get(side_b)
    if quote_a is None or quote_b is None:
        return None

    a_half_win = _home_marginal_is_half_win(line)
    stake_a, worst_case_profit = _quarter_line_worst_case(quote_a.decimal_odds, quote_b.decimal_odds, a_half_win)
    if worst_case_profit <= 0:
        return None

    legs = (
        Leg(selection=side_a, bookmaker=quote_a.bookmaker, decimal_odds=quote_a.decimal_odds),
        Leg(selection=side_b, bookmaker=quote_b.bookmaker, decimal_odds=quote_b.decimal_odds),
    )
    return ArbitrageResult(
        market=market,
        line=line,
        legs=legs,
        total_implied_probability=1.0 / (1.0 + worst_case_profit),
        push_possible=False,
        stake_fractions={side_a: stake_a, side_b: 1.0 - stake_a},
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


def allocate_stakes(result: ArbitrageResult, total_stake: float, allow_negative: bool = False) -> StakePlan:
    """Split ``total_stake`` across every leg so every outcome pays out the
    same guaranteed profit.

    ``allow_negative=True`` skips the "must actually be an arbitrage"
    guard and returns the plan anyway (with a negative
    ``guaranteed_profit``) — used by the manual what-if calculator, where
    showing "this combination guarantees a LOSS of X%" is itself the
    useful answer, not an error.
    """
    if total_stake <= 0:
        raise ValueError("total_stake must be positive")
    if not allow_negative and not result.is_arbitrage:
        raise ValueError("not an arbitrage: total implied probability >= 1.0")

    s = result.total_implied_probability
    legs = []
    for leg in result.legs:
        if result.stake_fractions is not None:
            fraction = result.stake_fractions[leg.selection]
        else:
            fraction = (1.0 / leg.decimal_odds) / s
        stake = total_stake * fraction
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
