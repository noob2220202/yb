from enum import Enum


class Sport(str, Enum):
    SOCCER = "soccer"
    BASKETBALL = "basketball"
    TENNIS = "tennis"


class MarketType(str, Enum):
    """Canonical market families every provider adapter must map into.

    Markets fall into two reliability tiers:

    - CORE (no push/void ambiguity, or push handled explicitly as a
      third outcome): safe to feed into the guaranteed-profit arbitrage
      solver.
    - EXOTIC: correct score / winning margin. These are almost never
      quoted identically across two independent books for the same
      match, so they don't produce true arbitrage. They're handled by
      the separate scoreline value-model instead (see
      app/engine/scoreline_model.py) which flags *expected-value edges*,
      not guaranteed profit.
    """

    MONEYLINE_3WAY = "moneyline_3way"  # home / draw / away
    MONEYLINE_2WAY = "moneyline_2way"  # home / away (no draw)
    TOTALS = "totals"  # over/under a line
    ASIAN_HANDICAP = "asian_handicap"
    BOTH_TEAMS_TO_SCORE = "btts"
    CORRECT_SCORE = "correct_score"
    WINNING_MARGIN = "winning_margin"

    @property
    def is_exotic(self) -> bool:
        return self in (MarketType.CORRECT_SCORE, MarketType.WINNING_MARGIN)


# Markets whose selections can never partially void (push) — a clean N-way
# partition of outcomes where the classic arbitrage formula applies directly.
CLEAN_PARTITION_MARKETS = {
    MarketType.MONEYLINE_3WAY,
    MarketType.MONEYLINE_2WAY,
    MarketType.BOTH_TEAMS_TO_SCORE,
}

# Markets where a whole-number line can push (stake refunded). Handled by
# modelling the push as its own outcome with odds fixed at 1.0 — see
# app/engine/arbitrage.py.
PUSH_RISK_MARKETS = {
    MarketType.TOTALS,
    MarketType.ASIAN_HANDICAP,
}
