"""System bet (시스템 베팅) math — combining several INDEPENDENT selections
(normally different matches; same-match legs are almost never independent,
see app/api/routes.py's cross-market warning) into every combination bet
of a chosen size range, rather than one all-or-nothing accumulator. A
"System M/N" bets every combination of size M, M+1, ..., N drawn from N
selections — losing some selections still pays out as long as at least M
of them win. Named products (Trixie = System 2/3, Yankee = System 2/4,
Canadian = System 2/5, Heinz = System 2/6, ...) are just this with a
fixed N and M=2.

IMPORTANT — unlike app/engine/arbitrage.py, this is NEVER a guaranteed-
profit tool, for two separate reasons:

1. The legs must be genuinely statistically independent for the hit-rate
   and expected-value math below to mean anything. Two markets on the
   SAME match essentially never are (see the mixed-market warning in
   app/api/routes.py) — this module trusts the caller to have supplied
   independent legs and cannot verify that itself.

2. More subtly: if a leg's "probability" is derived from the SAME odds
   you're betting at (probability = 1/odds), every combo bet's expected
   payout collapses to EXACTLY its own stake (since prod(p_i) * prod(odds_i)
   = prod(p_i * odds_i) = prod(1) = 1), making expected value trivially
   zero no matter what odds are entered. A genuine edge signal requires a
   probability estimate that is independent of the price you're betting
   at — your own judgement, or a sharper book's devigged number — which
   is why ``SystemLeg.probability`` is a separate field from
   ``decimal_odds``, never derived from it by this module.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

MAX_LEGS = 16  # 2**16 = 65536 combinations worst case (min_hits=1) -- keeps every brute-force loop below instant


@dataclass(frozen=True)
class SystemLeg:
    label: str
    bookmaker: str
    decimal_odds: float
    probability: float  # caller-supplied estimate in [0, 1], independent of decimal_odds


def poisson_binomial_at_least(probabilities: list[float], min_hits: int) -> float:
    """P(at least ``min_hits`` of these independent Bernoulli trials
    succeed). Standard O(n^2) DP over the exact-hit-count distribution
    (the "Poisson binomial" distribution -- a sum of independent but not
    necessarily identically-distributed Bernoulli trials)."""
    dp = [1.0]
    for p in probabilities:
        new_dp = [0.0] * (len(dp) + 1)
        for j, mass in enumerate(dp):
            if mass == 0.0:
                continue
            new_dp[j] += mass * (1.0 - p)
            new_dp[j + 1] += mass * p
        dp = new_dp
    min_hits = max(0, min(min_hits, len(dp) - 1))
    return sum(dp[min_hits:])


def smallest_min_hits_for_target(probabilities: list[float], target_hit_rate: float) -> int:
    """Largest M in [0, N] such that P(at least M hit) >= target -- i.e.
    the strictest (highest-margin) system that still clears the target
    hit rate. P(at least 0) is always 1.0, so this always returns some
    value; callers wanting a "real" system (not just "well, obviously")
    should clamp the result to at least 1 themselves.
    """
    n = len(probabilities)
    for m in range(n, -1, -1):
        if poisson_binomial_at_least(probabilities, m) >= target_hit_rate - 1e-9:
            return m
    return 0  # unreachable in practice (m=0 always satisfies), kept for type-checkers


@dataclass(frozen=True)
class SystemBetResult:
    num_selections: int
    min_hits: int
    num_bets: int
    unit_stake: float
    total_stake: float
    expected_profit: float
    expected_profit_percent: float
    best_case_profit: float
    best_case_profit_percent: float
    breakdown: list[tuple[int, int]]  # (combo_size, count), sizes min_hits..N


def build_system_bet(legs: list[SystemLeg], min_hits: int, total_stake: float) -> SystemBetResult:
    """A "System min_hits/N" bet: every combination of size min_hits..N
    from ``legs``, each combo an equal-stake accumulator of its own
    legs' odds. Returns the bet-count breakdown plus expected value
    (needs genuine ``leg.probability`` estimates -- see module
    docstring) and the best-case payout (every leg wins, so every combo
    bet pays out).
    """
    n = len(legs)
    effective_min = max(1, min(min_hits, n)) if n > 0 else 0

    breakdown: list[tuple[int, int]] = []
    num_bets = 0
    for size in range(effective_min, n + 1):
        count = math.comb(n, size)
        breakdown.append((size, count))
        num_bets += count

    if num_bets == 0:
        return SystemBetResult(
            num_selections=n,
            min_hits=min_hits,
            num_bets=0,
            unit_stake=0.0,
            total_stake=total_stake,
            expected_profit=-total_stake,
            expected_profit_percent=-100.0,
            best_case_profit=-total_stake,
            best_case_profit_percent=-100.0,
            breakdown=[],
        )

    unit_stake = total_stake / num_bets

    expected_payout = 0.0
    best_case_payout = 0.0
    for size in range(effective_min, n + 1):
        for combo in itertools.combinations(legs, size):
            combo_odds = math.prod(leg.decimal_odds for leg in combo)
            combo_prob = math.prod(leg.probability for leg in combo)
            expected_payout += unit_stake * combo_prob * combo_odds
            best_case_payout += unit_stake * combo_odds  # if ALL legs win, every combo bet wins too

    expected_profit = expected_payout - total_stake
    best_case_profit = best_case_payout - total_stake

    return SystemBetResult(
        num_selections=n,
        min_hits=effective_min,
        num_bets=num_bets,
        unit_stake=unit_stake,
        total_stake=total_stake,
        expected_profit=expected_profit,
        expected_profit_percent=(expected_profit / total_stake) * 100.0,
        best_case_profit=best_case_profit,
        best_case_profit_percent=(best_case_profit / total_stake) * 100.0,
        breakdown=breakdown,
    )
