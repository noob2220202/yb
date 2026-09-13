import itertools
import math
from math import comb

import pytest

from app.engine.systembet import (
    SystemLeg,
    build_system_bet,
    poisson_binomial_at_least,
    smallest_min_hits_for_target,
)


def _leg(label: str, odds: float, prob: float | None = None) -> SystemLeg:
    return SystemLeg(label=label, bookmaker="B", decimal_odds=odds, probability=prob if prob is not None else 1.0 / odds)


@pytest.mark.parametrize(
    "n,m,expected",
    [
        (3, 2, 4),  # Trixie
        (4, 2, 11),  # Yankee
        (5, 2, 26),  # Canadian / Super Yankee
        (6, 2, 57),  # Heinz
        (7, 2, 120),  # Super Heinz
        (8, 2, 247),  # Goliath
        (3, 1, 7),  # Patent (singles + doubles + treble)
        (4, 1, 15),  # Lucky 15
    ],
)
def test_named_system_bet_counts_match_real_products(n, m, expected):
    legs = [_leg(f"L{i}", 2.0, 0.5) for i in range(n)]
    result = build_system_bet(legs, m, 1000.0)
    assert result.num_bets == expected


def test_poisson_binomial_matches_classic_binomial_for_identical_probabilities():
    n, p = 10, 0.3
    for m in range(n + 1):
        pb = poisson_binomial_at_least([p] * n, m)
        binom = sum(comb(n, k) * p**k * (1 - p) ** (n - k) for k in range(m, n + 1))
        assert pb == pytest.approx(binom, abs=1e-9)


def test_poisson_binomial_at_least_zero_is_always_certain():
    assert poisson_binomial_at_least([0.1, 0.2, 0.9], 0) == pytest.approx(1.0)


def test_expected_value_collapses_to_breakeven_when_probability_equals_naive_implied():
    """The circularity trap this module's docstring warns about: using
    1/odds as the probability makes EVERY combo bet exactly breakeven in
    expectation, regardless of the actual odds -- there is no way to
    manufacture a real edge signal from a single price alone."""
    legs = [_leg(f"L{i}", odds) for i, odds in enumerate([2.1, 3.4, 1.8, 5.0, 2.7])]
    result = build_system_bet(legs, 2, 100_000.0)
    assert result.expected_profit_percent == pytest.approx(0.0, abs=1e-6)


def test_expected_value_is_positive_with_a_genuine_edge():
    # Odds imply 50% (evens), but the caller believes the true chance is 55%.
    legs = [_leg(f"L{i}", 2.0, 0.55) for i in range(4)]
    result = build_system_bet(legs, 2, 100_000.0)
    assert result.expected_profit_percent > 0


def test_expected_value_is_negative_with_a_pessimistic_estimate():
    legs = [_leg(f"L{i}", 2.0, 0.40) for i in range(4)]
    result = build_system_bet(legs, 2, 100_000.0)
    assert result.expected_profit_percent < 0


def test_best_case_payout_matches_brute_force_all_legs_win_scenario():
    legs = [
        _leg("A", 2.1, 0.5),
        _leg("B", 3.3, 0.35),
        _leg("C", 1.9, 0.55),
        _leg("D", 4.4, 0.25),
    ]
    min_hits = 2
    result = build_system_bet(legs, min_hits, 10_000.0)
    unit = result.unit_stake
    brute_force_payout = sum(
        unit * math.prod(leg.decimal_odds for leg in combo)
        for size in range(min_hits, len(legs) + 1)
        for combo in itertools.combinations(legs, size)
    )
    assert brute_force_payout == pytest.approx(result.best_case_profit + result.total_stake, abs=1e-6)


def test_smallest_min_hits_decreases_as_target_hit_rate_rises():
    probs = [0.6, 0.6, 0.6, 0.6, 0.6]
    m_loose = smallest_min_hits_for_target(probs, 0.5)
    m_strict = smallest_min_hits_for_target(probs, 0.95)
    assert m_strict <= m_loose


def test_unit_stake_splits_evenly_across_every_combo_bet():
    legs = [_leg(f"L{i}", 2.5, 0.4) for i in range(5)]
    result = build_system_bet(legs, 3, 9000.0)
    assert result.unit_stake * result.num_bets == pytest.approx(9000.0)
    assert result.num_bets == comb(5, 3) + comb(5, 4) + comb(5, 5)


def test_stake_split_across_breakdown_sums_to_total_bets():
    legs = [_leg(f"L{i}", 2.0, 0.5) for i in range(6)]
    result = build_system_bet(legs, 2, 5000.0)
    assert sum(count for _size, count in result.breakdown) == result.num_bets
    assert [size for size, _count in result.breakdown] == [2, 3, 4, 5, 6]
