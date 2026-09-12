import pytest

from app.engine.scoreline_model import (
    calibrate,
    correct_score_probability,
    devig_three_way,
    devig_two_way,
    find_cross_line_edges,
    find_value_edges,
    handicap_probabilities,
    implied_1x2,
    score_matrix,
    totals_probabilities,
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


def test_totals_probabilities_sum_to_one_and_classify_push_correctly():
    matrix = score_matrix(1.5, 1.1)

    p_over, p_under, p_push = totals_probabilities(matrix, 2.5)
    assert p_over + p_under + p_push == pytest.approx(1.0)
    assert p_push == 0.0  # half line can never push

    p_over, p_under, p_push = totals_probabilities(matrix, 2.0)
    assert p_over + p_under + p_push == pytest.approx(1.0)
    assert p_push > 0.0  # whole-number line can push


def test_handicap_probabilities_sum_to_one_and_symmetric_at_zero():
    matrix = score_matrix(1.3, 1.3)  # equal strength

    p_home, p_away, p_push = handicap_probabilities(matrix, 0.0)
    assert p_home + p_away + p_push == pytest.approx(1.0)
    assert p_home == pytest.approx(p_away, abs=1e-9)

    p_home, p_away, p_push = handicap_probabilities(matrix, -0.5)
    assert p_push == 0.0
    assert p_home + p_away == pytest.approx(1.0)


def test_find_cross_line_edges_flags_mispriced_secondary_line_only():
    matrix = score_matrix(1.6, 1.0)

    # 캘리브레이션에 쓰인 라인(2.5)은 정의상 모델과 거의 일치하므로 제외돼야 함.
    p_over_25, p_under_25, _ = totals_probabilities(matrix, 2.5)
    fair_over_25 = 1.0 / (p_over_25 / (p_over_25 + p_under_25))

    # 다른 라인(1.5)은 일부러 모델 대비 후하게 가격을 매겨서 엣지가 나오도록 함.
    p_over_15, p_under_15, _ = totals_probabilities(matrix, 1.5)
    fair_over_15 = 1.0 / (p_over_15 / (p_over_15 + p_under_15))
    generous_over_15 = fair_over_15 * 1.4

    edges = find_cross_line_edges(
        matrix,
        totals_quotes=[
            ("Pinnacle", 2.5, "over", round(fair_over_25, 2)),
            ("Pinnacle", 1.5, "over", round(generous_over_15, 2)),
        ],
        handicap_quotes=[],
        calibration_totals_line=2.5,
        min_edge_percent=2.0,
    )

    assert len(edges) == 1
    assert edges[0].line == 1.5
    assert edges[0].market == "totals"
    assert edges[0].edge_percent > 2.0


def test_find_cross_line_edges_handles_handicap_quotes():
    matrix = score_matrix(1.6, 1.0)
    p_home, p_away, _ = handicap_probabilities(matrix, -1.5)
    fair_home = 1.0 / (p_home / (p_home + p_away))
    generous_home = fair_home * 1.3

    edges = find_cross_line_edges(
        matrix,
        totals_quotes=[],
        handicap_quotes=[("Pinnacle", -1.5, "home", round(generous_home, 2))],
        min_edge_percent=2.0,
    )
    assert len(edges) == 1
    assert edges[0].market == "asian_handicap"
    assert edges[0].selection == "home"
