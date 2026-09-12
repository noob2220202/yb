"""Poisson/Dixon-Coles scoreline model — the "exotic markets" half of the
product (correct score, winning margin).

IMPORTANT — this module does NOT produce guaranteed profit. Unlike
``app/engine/arbitrage.py`` (which only ever combines odds for the *same*
market/outcome across independent books), this model estimates a full
scoreline probability grid for a match from its 1X2 and Totals odds, then
compares that model's probabilities against whatever price a book quotes
for an exotic selection (correct score, winning margin). A mismatch is an
*expected-value edge*, not a sure thing: the model can be wrong, and a
single bet on a 15/1 correct score loses money far more often than it
wins even when the model says it's +EV. Treat ``ValueEdge`` output as a
ranked shortlist worth a human's judgement, never as an auto-bet signal.

Method: independent Poisson goal counts for each side, corrected for the
well-documented under-dispersion of low scores (0-0, 1-0, 0-1, 1-1) using
the Dixon-Coles (1997) tau adjustment with a fixed rho. Home/away scoring
rates (lambda_home, lambda_away) are calibrated by least-squares fit
against the match's own devigged 1X2 and Totals prices, so the model is
always anchored to the market's own view of team strength rather than any
external rating system.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize

RHO = -0.08  # standard literature value; low scores are slightly less
             # correlated-independent than a pure Poisson product implies.
MAX_GOALS = 8


def devig_two_way(odds_a: float, odds_b: float) -> tuple[float, float]:
    ia, ib = 1.0 / odds_a, 1.0 / odds_b
    total = ia + ib
    return ia / total, ib / total


def devig_three_way(odds_home: float, odds_draw: float, odds_away: float) -> tuple[float, float, float]:
    ih, idr, ia = 1.0 / odds_home, 1.0 / odds_draw, 1.0 / odds_away
    total = ih + idr + ia
    return ih / total, idr / total, ia / total


def _poisson_pmf(k: int, lam: float) -> float:
    return math.exp(-lam) * lam**k / math.factorial(k)


def _dixon_coles_tau(x: int, y: int, lam: float, mu: float, rho: float) -> float:
    if x == 0 and y == 0:
        return 1 - lam * mu * rho
    if x == 0 and y == 1:
        return 1 + lam * rho
    if x == 1 and y == 0:
        return 1 + mu * rho
    if x == 1 and y == 1:
        return 1 - rho
    return 1.0


def score_matrix(lam_home: float, lam_away: float, rho: float = RHO, max_goals: int = MAX_GOALS) -> np.ndarray:
    """Returns an (max_goals+1) x (max_goals+1) matrix P[h][a] of the
    probability of a final score of h-a, normalized to sum to 1.
    """
    matrix = np.zeros((max_goals + 1, max_goals + 1))
    for h in range(max_goals + 1):
        ph = _poisson_pmf(h, lam_home)
        for a in range(max_goals + 1):
            pa = _poisson_pmf(a, lam_away)
            tau = _dixon_coles_tau(h, a, lam_home, lam_away, rho)
            matrix[h, a] = ph * pa * tau
    matrix = np.clip(matrix, 0, None)
    matrix /= matrix.sum()
    return matrix


def implied_1x2(matrix: np.ndarray) -> tuple[float, float, float]:
    home = float(np.tril(matrix, -1).sum())
    draw = float(np.trace(matrix))
    away = float(np.triu(matrix, 1).sum())
    return home, draw, away


def implied_over(matrix: np.ndarray, line: float) -> float:
    size = matrix.shape[0]
    total = 0.0
    for h in range(size):
        for a in range(size):
            if h + a > line:
                total += matrix[h, a]
    return total


@dataclass(frozen=True)
class CalibratedMatch:
    lam_home: float
    lam_away: float
    rho: float
    matrix: np.ndarray


def calibrate(
    home_odds: float,
    draw_odds: float,
    away_odds: float,
    totals_line: float,
    over_odds: float,
    under_odds: float,
) -> CalibratedMatch:
    """Fits lambda_home, lambda_away and the Dixon-Coles rho jointly so the
    model matches the match's own devigged 1X2 *and* Totals prices — three
    independent targets (1X2 has two degrees of freedom since it sums to 1,
    plus one from Totals), matched by three free parameters.
    """
    p_home, p_draw, p_away = devig_three_way(home_odds, draw_odds, away_odds)
    p_over, _p_under = devig_two_way(over_odds, under_odds)

    def objective(params: np.ndarray) -> float:
        lam_home, lam_away, rho = params
        matrix = score_matrix(lam_home, lam_away, rho)
        h, d, a = implied_1x2(matrix)
        o = implied_over(matrix, totals_line)
        return (h - p_home) ** 2 + (d - p_draw) ** 2 + (a - p_away) ** 2 + (o - p_over) ** 2

    result = minimize(
        objective,
        x0=np.array([1.3, 1.2, RHO]),
        bounds=[(0.05, 6.0), (0.05, 6.0), (-0.3, 0.3)],
        method="L-BFGS-B",
        options={"ftol": 1e-12, "gtol": 1e-10},
    )
    lam_home, lam_away, rho = result.x
    return CalibratedMatch(lam_home=lam_home, lam_away=lam_away, rho=rho, matrix=score_matrix(lam_home, lam_away, rho))


def correct_score_probability(matrix: np.ndarray, home_goals: int, away_goals: int) -> float:
    size = matrix.shape[0]
    if home_goals >= size or away_goals >= size:
        return 0.0
    return float(matrix[home_goals, away_goals])


def winning_margin_buckets(matrix: np.ndarray) -> dict[str, float]:
    """Buckets: home_by_1/2/3plus, draw, away_by_1/2/3plus."""
    size = matrix.shape[0]
    buckets = {
        "home_by_1": 0.0,
        "home_by_2": 0.0,
        "home_by_3plus": 0.0,
        "draw": 0.0,
        "away_by_1": 0.0,
        "away_by_2": 0.0,
        "away_by_3plus": 0.0,
    }
    for h in range(size):
        for a in range(size):
            diff = h - a
            p = matrix[h, a]
            if diff == 0:
                buckets["draw"] += p
            elif diff == 1:
                buckets["home_by_1"] += p
            elif diff == 2:
                buckets["home_by_2"] += p
            elif diff >= 3:
                buckets["home_by_3plus"] += p
            elif diff == -1:
                buckets["away_by_1"] += p
            elif diff == -2:
                buckets["away_by_2"] += p
            else:
                buckets["away_by_3plus"] += p
    return buckets


@dataclass(frozen=True)
class ValueEdge:
    selection: str
    bookmaker: str
    decimal_odds: float
    model_probability: float
    implied_probability: float
    edge_percent: float


def find_value_edges(
    matrix: np.ndarray,
    correct_score_quotes: list[tuple[str, str, float]],
    margin_quotes: list[tuple[str, str, float]],
    min_edge_percent: float = 2.0,
) -> list[ValueEdge]:
    """``correct_score_quotes`` / ``margin_quotes`` are
    (bookmaker, selection, decimal_odds) tuples. Selection format:
    correct score "H-A" (e.g. "2-1"); margin one of the
    ``winning_margin_buckets`` keys.
    """
    edges: list[ValueEdge] = []
    margins = winning_margin_buckets(matrix)

    for bookmaker, selection, odds in correct_score_quotes:
        try:
            h_str, a_str = selection.split("-")
            model_p = correct_score_probability(matrix, int(h_str), int(a_str))
        except (ValueError, IndexError):
            continue
        edges.append(_build_edge(bookmaker, selection, odds, model_p))

    for bookmaker, selection, odds in margin_quotes:
        model_p = margins.get(selection)
        if model_p is None:
            continue
        edges.append(_build_edge(bookmaker, selection, odds, model_p))

    edges = [e for e in edges if e.edge_percent >= min_edge_percent]
    edges.sort(key=lambda e: e.edge_percent, reverse=True)
    return edges


def _build_edge(bookmaker: str, selection: str, odds: float, model_p: float) -> ValueEdge:
    implied = 1.0 / odds
    edge = model_p * odds - 1.0
    return ValueEdge(
        selection=selection,
        bookmaker=bookmaker,
        decimal_odds=odds,
        model_probability=model_p,
        implied_probability=implied,
        edge_percent=edge * 100.0,
    )
