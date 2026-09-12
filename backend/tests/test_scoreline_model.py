import pytest

from app.engine.scoreline_model import (
    calibrate,
    correct_score_probability,
    devig_three_way,
    devig_two_way,
    find_value_edges,
    implied_1x2,
    score_matrix,
    winning_margin_buckets,
)


def test_devig_two_way_sums_to_one():
    p_a, p_b = devig_two_way(1.90, 1.90)
    assert p_a == pytest.approx(0.5, abs=1e-9)
    assert p_a + p_b == pytest.approx(1.0)


def test_devig_three_way_sums_to_one():
    p_h, p_d, p_a = devig_three_way(2.10, 3.40, 4.00)
    assert p_h + p_d + p_a == pytest.approx(1.0)
    assert p_h > p_a  # home is the shorter-priced favorite


def test_score_matrix_sums_to_one():
    matrix = score_matrix(1.4, 1.1)
    assert matrix.sum() == pytest.approx(1.0)


def test_symmetric_strength_gives_near_equal_home_away_win_prob():
    matrix = score_matrix(1.3, 1.3)
    home, draw, away = implied_1x2(matrix)
    # Home advantage isn't modelled explicitly, so equal lambdas should be
    # (near) symmetric between home and away.
    assert home == pytest.approx(away, abs=1e-9)
    assert draw > 0


def test_calibration_recovers_market_implied_probabilities():
    home_odds, draw_odds, away_odds = 2.10, 3.40, 4.00
    totals_line, over_odds, under_odds = 2.5, 1.95, 1.95

    calibrated = calibrate(home_odds, draw_odds, away_odds, totals_line, over_odds, under_odds)
    model_home, model_draw, model_away = implied_1x2(calibrated.matrix)

    target_home, target_draw, target_away = devig_three_way(home_odds, draw_odds, away_odds)
    assert model_home == pytest.approx(target_home, abs=0.01)
    assert model_draw == pytest.approx(target_draw, abs=0.01)
    assert model_away == pytest.approx(target_away, abs=0.01)


def test_winning_margin_buckets_sum_to_one():
    matrix = score_matrix(1.6, 1.0)
    buckets = winning_margin_buckets(matrix)
    assert sum(buckets.values()) == pytest.approx(1.0)


def test_correct_score_probability_matches_matrix_cell():
    matrix = score_matrix(1.5, 1.1)
    assert correct_score_probability(matrix, 1, 0) == pytest.approx(matrix[1, 0])
    assert correct_score_probability(matrix, 20, 20) == 0.0


def test_find_value_edges_flags_generous_price_and_skips_fair_one():
    matrix = score_matrix(1.6, 1.0)
    fair_prob = correct_score_probability(matrix, 1, 0)
    fair_odds = 1.0 / fair_prob  # zero-margin fair price -> no edge
    generous_odds = fair_odds * 1.5  # 50% above fair -> should be flagged

    edges = find_value_edges(
        matrix,
        correct_score_quotes=[
            ("FairBook", "1-0", round(fair_odds, 2)),
            ("SoftBook", "1-0", round(generous_odds, 2)),
        ],
        margin_quotes=[],
        min_edge_percent=2.0,
    )
    assert len(edges) == 1
    assert edges[0].bookmaker == "SoftBook"
    assert edges[0].edge_percent > 2.0
