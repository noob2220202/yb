"""Synthetic odds fixtures — TEST-ONLY.

This used to live at ``app/providers/demo.py`` and ship as a real,
selectable provider (``USE_DEMO_PROVIDER=true``) so the app had something
to show with zero API keys. That dummy-data path has been removed from
the running app entirely: with no real provider configured, the app now
correctly shows an empty state rather than fabricated picks. This module
keeps the same hand-picked, internally-consistent numbers around purely
so the scanner/math tests still have deterministic input without needing
live credentials.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.enums import MarketType, Sport
from app.core.schemas import NormalizedEvent, OddsQuote
from app.providers.base import OddsProvider

_now = datetime.now(timezone.utc)

ARSENAL_CHELSEA = NormalizedEvent(
    sport=Sport.SOCCER,
    home_team="Arsenal",
    away_team="Chelsea",
    commence_time=_now + timedelta(hours=6),
    league="EPL (fixture)",
)

LAKERS_CELTICS = NormalizedEvent(
    sport=Sport.BASKETBALL,
    home_team="Lakers",
    away_team="Celtics",
    commence_time=_now + timedelta(hours=10),
    league="NBA (fixture)",
)

CITY_NEWCASTLE = NormalizedEvent(
    sport=Sport.SOCCER,
    home_team="Man City",
    away_team="Newcastle",
    commence_time=_now + timedelta(hours=8),
    league="EPL (fixture)",
)


def arsenal_chelsea_quotes() -> list[OddsQuote]:
    e = ARSENAL_CHELSEA
    q: list[OddsQuote] = []
    # 3-way moneyline: best home@BookA(2.10), draw@BookB(3.60), away@BookC(4.50)
    # sum(1/2.10 + 1/3.60 + 1/4.50) = 0.9762 -> ~2.4% guaranteed margin.
    for book, home, draw, away in [
        ("FixtureBookA", 2.10, 3.40, 4.00),
        ("FixtureBookB", 1.95, 3.60, 4.20),
        ("FixtureBookC", 2.05, 3.50, 4.50),
    ]:
        q.append(OddsQuote(e, book, MarketType.MONEYLINE_3WAY, "home", home))
        q.append(OddsQuote(e, book, MarketType.MONEYLINE_3WAY, "draw", draw))
        q.append(OddsQuote(e, book, MarketType.MONEYLINE_3WAY, "away", away))

    # Totals 2.5: best over@BookB(2.05), best under@BookC(2.00) -> ~1.2% margin.
    for book, over, under in [
        ("FixtureBookA", 1.95, 1.95),
        ("FixtureBookB", 2.05, 1.85),
        ("FixtureBookC", 1.90, 2.00),
    ]:
        q.append(OddsQuote(e, book, MarketType.TOTALS, "over", over, line=2.5))
        q.append(OddsQuote(e, book, MarketType.TOTALS, "under", under, line=2.5))

    # Asian handicap -0.5 (home): best home@BookB(2.00), best away@BookC(2.05).
    for book, home, away in [
        ("FixtureBookA", 1.90, 1.95),
        ("FixtureBookB", 2.00, 1.85),
        ("FixtureBookC", 1.85, 2.05),
    ]:
        q.append(OddsQuote(e, book, MarketType.ASIAN_HANDICAP, "home", home, line=-0.5))
        q.append(OddsQuote(e, book, MarketType.ASIAN_HANDICAP, "away", away, line=-0.5))

    return q


def lakers_celtics_quotes() -> list[OddsQuote]:
    e = LAKERS_CELTICS
    q: list[OddsQuote] = []
    # 2-way moneyline: best home@BookB(2.05), best away@BookA(2.05) -> ~2.4% margin.
    for book, home, away in [
        ("FixtureBookA", 1.90, 2.05),
        ("FixtureBookB", 2.05, 1.90),
    ]:
        q.append(OddsQuote(e, book, MarketType.MONEYLINE_2WAY, "home", home))
        q.append(OddsQuote(e, book, MarketType.MONEYLINE_2WAY, "away", away))
    return q


def city_newcastle_quotes() -> list[OddsQuote]:
    e = CITY_NEWCASTLE
    q: list[OddsQuote] = []
    # A single book's ordinary 1X2 + totals prices (with normal overround,
    # not an arbitrage) used to calibrate the scoreline model...
    q.append(OddsQuote(e, "FixtureBookA", MarketType.MONEYLINE_3WAY, "home", 1.50))
    q.append(OddsQuote(e, "FixtureBookA", MarketType.MONEYLINE_3WAY, "draw", 4.50))
    q.append(OddsQuote(e, "FixtureBookA", MarketType.MONEYLINE_3WAY, "away", 6.50))
    q.append(OddsQuote(e, "FixtureBookA", MarketType.TOTALS, "over", 1.90, line=2.5))
    q.append(OddsQuote(e, "FixtureBookA", MarketType.TOTALS, "under", 1.95, line=2.5))
    # ...plus a soft book's exotic-market prices. "2-1" is priced well
    # above the model's fair odds (~10.3) so the value-edge scanner has
    # something to find; "1-0" and the margin price are near/below fair,
    # so they should NOT be flagged.
    q.append(OddsQuote(e, "FixtureBookSoft", MarketType.CORRECT_SCORE, "2-1", 13.00))
    q.append(OddsQuote(e, "FixtureBookSoft", MarketType.CORRECT_SCORE, "1-0", 7.00))
    q.append(OddsQuote(e, "FixtureBookSoft", MarketType.WINNING_MARGIN, "home_by_1", 3.20))

    # Same-bookmaker cross-line fixture (works with a single provider, no
    # second book needed): FixtureBookA's OWN secondary lines on this
    # match, calibrated against its own 2.5 total above. Only one side
    # per line is quoted here on purpose -- with zero-vig "fair" prices,
    # quoting both sides of the same line from the same book would sum to
    # ~1 and could round into looking like a same-book arbitrage, which
    # doesn't happen in reality (see scanner.py) and would be a
    # misleading fixture.
    # Totals 1.5 "under" is priced fair (~4.02) -> should NOT be flagged.
    q.append(OddsQuote(e, "FixtureBookA", MarketType.TOTALS, "under", 4.02, line=1.5))
    # Totals 3.5 "over" is priced well above its own fair odds (~3.50) -> SHOULD be flagged.
    q.append(OddsQuote(e, "FixtureBookA", MarketType.TOTALS, "over", 4.72, line=3.5))
    # Handicap -1.5 "away" is priced fair (~1.63) -> should NOT be flagged.
    q.append(OddsQuote(e, "FixtureBookA", MarketType.ASIAN_HANDICAP, "away", 1.63, line=-1.5))
    return q


def demo_quotes(sports: list[Sport]) -> list[OddsQuote]:
    """All fixture quotes across every synthetic event, filtered to the
    requested sports -- mirrors the old ``DemoProvider().fetch()`` shape
    for easy test call-site migration."""
    quotes = arsenal_chelsea_quotes() + lakers_celtics_quotes() + city_newcastle_quotes()
    return [q for q in quotes if q.event.sport in sports]


class FakeProvider(OddsProvider):
    """A minimal OddsProvider stand-in for monkeypatching
    ``app.scheduler.build_providers`` in tests."""

    def __init__(self, name: str, quotes: list[OddsQuote]):
        self.name = name
        self._quotes = quotes

    async def fetch(self, sports: list[Sport]) -> list[OddsQuote]:
        return [q for q in self._quotes if q.event.sport in sports]


class FixtureProvider(FakeProvider):
    """A FakeProvider preloaded with the full ``demo_quotes`` fixture set."""

    def __init__(self):
        super().__init__("fixture", arsenal_chelsea_quotes() + lakers_celtics_quotes() + city_newcastle_quotes())
