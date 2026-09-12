from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.core.enums import MarketType, Sport


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class NormalizedEvent:
    """A match/fixture, keyed so the same real-world event can be matched
    across independent providers even though each one assigns its own id.
    """

    sport: Sport
    home_team: str
    away_team: str
    commence_time: datetime
    league: str = ""

    @property
    def event_key(self) -> str:
        def norm(s: str) -> str:
            return " ".join(s.strip().lower().split())

        return f"{self.sport.value}:{norm(self.home_team)}-vs-{norm(self.away_team)}:{self.commence_time.date().isoformat()}"


@dataclass(frozen=True)
class OddsQuote:
    """A single priced selection, as emitted by a provider adapter.

    ``line`` is the point/total for markets that need one (e.g. 2.5 for
    Totals, -1.5 for Asian Handicap) and ``None`` otherwise. ``selection``
    is a canonical label within the market (e.g. "home", "draw", "away",
    "over", "under", "push").
    """

    event: NormalizedEvent
    bookmaker: str
    market: MarketType
    selection: str
    decimal_odds: float
    line: float | None = None
    fetched_at: datetime = field(default_factory=utcnow)

    @property
    def market_key(self) -> tuple[str, MarketType, float | None]:
        return (self.event.event_key, self.market, self.line)
