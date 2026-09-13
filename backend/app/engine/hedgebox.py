"""Two-leg hedge box calculator.

The idea (this project's current core product): pick TWO selections on
ONE match that you, the admin, judge together cover close to 100% of
what can realistically happen (e.g. "Draw" + "Home -0.5 Asian Handicap"
covers every outcome except an outright away win) and size both stakes
so the payout is identical no matter which of the two hits. Unlike
this project's previous clean-partition arbitrage engine, this module
never checks that the two selections are the WHOLE outcome space --
that judgment call (is the excluded outcome rare enough to accept) is
the admin's alone. This is a deliberate, informed hedge, not a
guaranteed-profit signal.

Sizing is driven by a FIXED PROFIT TARGET in currency, not a margin
percentage: given the two prices and a target net profit, the required
payout (and therefore both stakes) is solved backwards so that
whichever leg hits, the net profit comes out to (approximately, after
stake rounding) that target amount. This mirrors how commercial
tipster "box" products present a pick -- "bet these amounts, walk away
with ~10,000 either way" -- rather than a percentage return.

When the price of the deliberately-excluded outcome is also supplied
(never bet, just for reference), this devigs across all three prices
to report an *estimated* hit rate, purely as a sanity check that the
admin isn't leaving out something too likely.
"""

from __future__ import annotations

from dataclasses import dataclass


class HedgeBoxError(ValueError):
    """Raised when the requested hedge box cannot be built -- always a
    user-facing message, never a bug report."""


@dataclass(frozen=True)
class HedgeBoxResult:
    leg_a_stake: float
    leg_b_stake: float
    total_stake: float
    leg_a_payout: float
    leg_b_payout: float
    guaranteed_profit: float
    profit_percent: float
    implied_hit_rate_percent: float | None


def calculate_hedge_box(
    odds_a: float,
    odds_b: float,
    target_profit: float,
    stake_round_to: float = 100.0,
    excluded_odds: float | None = None,
) -> HedgeBoxResult:
    """Back-solves both stakes so profit is the same (~= ``target_profit``)
    whichever of the two legs wins.

    Equalizing payout means ``stake_a * odds_a == stake_b * odds_b ==
    payout``, so ``total_stake = payout * (1/odds_a + 1/odds_b)`` and
    ``profit = payout - total_stake = payout * (1 - p)`` where ``p`` is
    that implied-probability sum. Solving for the payout that yields the
    target profit: ``payout = target_profit / (1 - p)``. This only has a
    positive solution when ``p < 1`` -- i.e. the two prices you picked
    already imply less than 100% combined probability, which is exactly
    what "deliberately excluding a real, priced-in outcome from a fairly
    priced market" looks like numerically.
    """
    if odds_a <= 1.0 or odds_b <= 1.0:
        raise HedgeBoxError("배당은 1보다 커야 합니다.")
    if target_profit <= 0:
        raise HedgeBoxError("목표 순이익은 0보다 커야 합니다.")
    if stake_round_to <= 0:
        raise HedgeBoxError("반올림 단위는 0보다 커야 합니다.")

    implied_sum = 1.0 / odds_a + 1.0 / odds_b
    if implied_sum >= 1.0:
        raise HedgeBoxError(
            "이 두 배당의 조합으로는 목표 순이익을 만들 수 없습니다 — 두 배당의 역수 합이 "
            "1 이상이라 실제로는 마진이 아니라 손실 구간입니다. 더 높은 배당 조합을 고르세요."
        )

    payout = target_profit / (1.0 - implied_sum)
    stake_a = round(payout / odds_a / stake_round_to) * stake_round_to
    stake_b = round(payout / odds_b / stake_round_to) * stake_round_to

    total_stake = stake_a + stake_b
    if total_stake <= 0:
        raise HedgeBoxError("배팅금이 0 이하로 반올림되었습니다 — 목표 순이익을 늘리거나 반올림 단위를 줄이세요.")

    leg_a_payout = stake_a * odds_a
    leg_b_payout = stake_b * odds_b
    guaranteed_profit = min(leg_a_payout, leg_b_payout) - total_stake
    profit_percent = (guaranteed_profit / total_stake) * 100.0

    implied_hit_rate_percent: float | None = None
    if excluded_odds is not None and excluded_odds > 1.0:
        inv_a, inv_b, inv_x = 1.0 / odds_a, 1.0 / odds_b, 1.0 / excluded_odds
        total_inv = inv_a + inv_b + inv_x
        implied_hit_rate_percent = (inv_a + inv_b) / total_inv * 100.0

    return HedgeBoxResult(
        leg_a_stake=stake_a,
        leg_b_stake=stake_b,
        total_stake=total_stake,
        leg_a_payout=leg_a_payout,
        leg_b_payout=leg_b_payout,
        guaranteed_profit=guaranteed_profit,
        profit_percent=profit_percent,
        implied_hit_rate_percent=implied_hit_rate_percent,
    )
