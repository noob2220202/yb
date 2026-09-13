import pytest

from app.engine.hedgebox import HedgeBoxError, calculate_hedge_box


def test_calculate_hedge_box_matches_reference_example():
    """Reproduces the reference product's real numbers: Draw @3.75 +
    Home -0.5 @1.909, target profit 10,000 KRW."""
    result = calculate_hedge_box(odds_a=3.75, odds_b=1.909, target_profit=10000, stake_round_to=100.0)
    assert result.leg_a_stake > 0
    assert result.leg_b_stake > 0
    assert result.total_stake == pytest.approx(37900, abs=500)
    assert result.guaranteed_profit == pytest.approx(10000, rel=0.05)


def test_profit_is_identical_whichever_leg_hits():
    result = calculate_hedge_box(odds_a=2.5, odds_b=1.8, target_profit=5000, stake_round_to=10.0)
    payout_a = result.leg_a_stake * 2.5
    payout_b = result.leg_b_stake * 1.8
    profit_a = payout_a - result.total_stake
    profit_b = payout_b - result.total_stake
    # Equal up to the rounding step's resolution on each leg.
    assert profit_a == pytest.approx(profit_b, abs=20)
    assert min(profit_a, profit_b) == pytest.approx(result.guaranteed_profit, abs=1e-6)


def test_larger_target_profit_scales_stakes_up():
    small = calculate_hedge_box(odds_a=2.0, odds_b=2.5, target_profit=1000, stake_round_to=1.0)
    big = calculate_hedge_box(odds_a=2.0, odds_b=2.5, target_profit=10000, stake_round_to=1.0)
    assert big.total_stake > small.total_stake
    assert big.guaranteed_profit == pytest.approx(10 * small.guaranteed_profit, rel=0.01)


def test_rejects_odds_at_or_below_one():
    with pytest.raises(HedgeBoxError):
        calculate_hedge_box(odds_a=1.0, odds_b=2.0, target_profit=1000)
    with pytest.raises(HedgeBoxError):
        calculate_hedge_box(odds_a=2.0, odds_b=0.5, target_profit=1000)


def test_rejects_non_positive_target_profit():
    with pytest.raises(HedgeBoxError):
        calculate_hedge_box(odds_a=2.0, odds_b=2.0, target_profit=0)
    with pytest.raises(HedgeBoxError):
        calculate_hedge_box(odds_a=2.0, odds_b=2.0, target_profit=-100)


def test_rejects_combo_whose_implied_probability_sum_is_at_or_above_one():
    """Two prices this short-priced together can never fund a positive
    profit target no matter the stakes -- this is the sanity check that
    stops a nonsensical combo from silently returning garbage stakes."""
    with pytest.raises(HedgeBoxError):
        calculate_hedge_box(odds_a=1.5, odds_b=1.5, target_profit=1000)  # 1/1.5+1/1.5 = 1.333 >= 1


def test_estimated_hit_rate_uses_devig_across_all_three_prices():
    result = calculate_hedge_box(odds_a=3.75, odds_b=1.909, target_profit=10000, excluded_odds=6.0)
    inv_a, inv_b, inv_x = 1 / 3.75, 1 / 1.909, 1 / 6.0
    expected = (inv_a + inv_b) / (inv_a + inv_b + inv_x) * 100.0
    assert result.implied_hit_rate_percent == pytest.approx(expected)


def test_no_excluded_odds_means_no_hit_rate_estimate():
    result = calculate_hedge_box(odds_a=3.0, odds_b=2.0, target_profit=1000)
    assert result.implied_hit_rate_percent is None


def test_stake_rounding_step_is_respected():
    result = calculate_hedge_box(odds_a=2.2, odds_b=2.1, target_profit=5000, stake_round_to=500.0)
    assert result.leg_a_stake == pytest.approx(round(result.leg_a_stake / 500.0) * 500.0, abs=1e-6)
    assert result.leg_b_stake == pytest.approx(round(result.leg_b_stake / 500.0) * 500.0, abs=1e-6)
