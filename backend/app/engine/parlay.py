"""Cross-match parlay (combo/다폴더) value scanner.

Scope, deliberately: only ever combines legs from DIFFERENT matches (a
traditional/cross-match parlay — 서로 다른 경기 조합), never two legs from
the same match. That keeps every leg's outcome genuinely independent of
every other leg's, so a parlay's true win probability is simply the
product of each leg's true probability — no correlation modeling needed.
Same-game parlays (SGP) are a different, harder problem (legs on the same
match are correlated — e.g. "home wins" and "over 2.5" aren't
independent) and are intentionally not built here.

A parlay's PRICE is always one specific bookmaker's own product of ITS
per-leg odds, because a real parlay ticket can only be placed at one
book — you can't mix bookmakers within one bet slip. Its FAIR probability
per leg instead comes from the best price available *anywhere* for that
exact outcome (across every configured bookmaker), proportionally devigged
(see ``devig_market``). Comparing "this book's own parlay price" against
"the market's best consensus fair probability" is the same value-edge
pattern as the rest of this project (see
app/engine/scoreline_model.py's module docstring) — **not arbitrage, not
guaranteed profit**. A parlay's own compounding of each leg's ordinary
bookmaker margin will usually make it look worse than fair value, which
is expected and not itself a finding; a genuine hit is when a book's own
per-leg prices are generous enough (or its parlay math doesn't reduce
enough for its own margins) that the combined price still clears fair
value despite that compounding.

Not built: hedging one parlay against another, or against a covering set
of singles, for a guaranteed outcome. That needs either a full 2^N grid
of complementary parlays or tracking a specific bet a user has actually
placed — a materially different (and more complex) feature than the
value-scanning done here.
"""

from __future__ import annotations

import itertools
import math
from collections import defaultdict
from dataclasses import dataclass

from app.core.enums import MarketType
from app.core.schemas import OddsQuote
from app.engine.arbitrage import best_odds_per_selection, expected_selections, is_supported_line

MAX_PARLAY_LEGS = 4
MIN_PARLAY_LEGS = 2
# A leg with a very low fair probability contributes little information
# (huge variance, and a single quoting error swings the whole parlay's
# edge estimate) -- skip legs the market itself thinks are longshots.
MIN_LEG_FAIR_PROBABILITY = 0.05

# Exotic markets (correct score, winning margin) go through the scoreline
# model instead (see scoreline_model.py) -- this module only combines the
# same clean-partition core markets the arbitrage engine uses.
_SUPPORTED_MARKETS = frozenset(
    {
        MarketType.MONEYLINE_3WAY,
        MarketType.MONEYLINE_2WAY,
        MarketType.BOTH_TEAMS_TO_SCORE,
        MarketType.TOTALS,
        MarketType.ASIAN_HANDICAP,
    }
)


def devig_market(odds_by_selection: dict[str, float]) -> dict[str, float]:
    """Proportional (multiplicative) devig: given every selection's best
    available decimal odds in one market, returns fair (no-vig)
    probabilities that sum to 1."""
    implied = {selection: 1.0 / odds for selection, odds in odds_by_selection.items()}
    total = sum(implied.values())
    return {selection: p / total for selection, p in implied.items()}


@dataclass(frozen=True)
class ParlayLegCandidate:
    event_key: str
    event_label: str
    market: MarketType
    line: float | None
    selection: str
    bookmaker: str
    decimal_odds: float
    fair_probability: float

    @property
    def individual_edge_percent(self) -> float:
        return (self.fair_probability * self.decimal_odds - 1.0) * 100.0


def build_parlay_candidates(quotes: list[OddsQuote]) -> list[ParlayLegCandidate]:
    """One candidate per (event, market, line, selection, bookmaker) that
    quoted it, each carrying the *market's* best-available-anywhere fair
    probability for that selection (not that specific bookmaker's own
    devigged probability -- see module docstring for why).
    """
    groups: dict[tuple[str, MarketType, float | None], list[OddsQuote]] = defaultdict(list)
    for q in quotes:
        groups[q.market_key].append(q)

    candidates: list[ParlayLegCandidate] = []
    for (event_key, market, line), group in groups.items():
        if market not in _SUPPORTED_MARKETS or not is_supported_line(market, line):
            continue
        required = expected_selections(market)
        if not required:
            continue

        best = best_odds_per_selection(group, required)
        if best is None:
            continue  # incomplete partition anywhere -- can't devig a fair value

        fair_probabilities = devig_market({selection: q.decimal_odds for selection, q in best.items()})
        event = group[0].event
        event_label = f"{event.home_team} vs {event.away_team}"

        for q in group:
            fair_p = fair_probabilities.get(q.selection)
            if fair_p is None:
                continue
            candidates.append(
                ParlayLegCandidate(
                    event_key=event_key,
                    event_label=event_label,
                    market=market,
                    line=line,
                    selection=q.selection,
                    bookmaker=q.bookmaker,
                    decimal_odds=q.decimal_odds,
                    fair_probability=fair_p,
                )
            )
    return candidates


@dataclass(frozen=True)
class ParlayResult:
    legs: tuple[ParlayLegCandidate, ...]
    bookmaker: str
    combined_odds: float
    combined_fair_probability: float

    @property
    def edge_percent(self) -> float:
        return (self.combined_fair_probability * self.combined_odds - 1.0) * 100.0


def find_parlay_value(
    candidates: list[ParlayLegCandidate],
    min_legs: int = MIN_PARLAY_LEGS,
    max_legs: int = MAX_PARLAY_LEGS,
    min_edge_percent: float = 5.0,
) -> list[ParlayResult]:
    """Groups candidates by bookmaker (a parlay ticket is single-book),
    keeps at most one leg per event per bookmaker (the one with the best
    individual edge, both to bound the combinatorics and because two legs
    from the same match would make this a same-game parlay -- out of
    scope, see module docstring), then checks every combination from
    ``min_legs`` to ``max_legs`` for combined value.
    """
    by_bookmaker: dict[str, list[ParlayLegCandidate]] = defaultdict(list)
    for c in candidates:
        if c.fair_probability >= MIN_LEG_FAIR_PROBABILITY:
            by_bookmaker[c.bookmaker].append(c)

    results: list[ParlayResult] = []
    for bookmaker, legs in by_bookmaker.items():
        best_per_event: dict[str, ParlayLegCandidate] = {}
        for leg in legs:
            current = best_per_event.get(leg.event_key)
            if current is None or leg.individual_edge_percent > current.individual_edge_percent:
                best_per_event[leg.event_key] = leg
        pool = list(best_per_event.values())

        upper = min(max_legs, len(pool))
        for size in range(min_legs, upper + 1):
            for combo in itertools.combinations(pool, size):
                combined_odds = math.prod(leg.decimal_odds for leg in combo)
                combined_fair = math.prod(leg.fair_probability for leg in combo)
                edge_percent = (combined_fair * combined_odds - 1.0) * 100.0
                if edge_percent >= min_edge_percent:
                    results.append(
                        ParlayResult(
                            legs=combo,
                            bookmaker=bookmaker,
                            combined_odds=combined_odds,
                            combined_fair_probability=combined_fair,
                        )
                    )

    results.sort(key=lambda r: r.edge_percent, reverse=True)
    return results
