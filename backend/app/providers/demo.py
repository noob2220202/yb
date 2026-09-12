"""Synthetic fixture provider.

Lets the whole stack (scanner, API, dashboard) run and demonstrably find a
real arbitrage and a real value-edge with zero external accounts, so
``USE_DEMO_PROVIDER=true`` gives a working local demo out of the box.
The odds below are hand-picked but internally consistent — the arbitrage
margins are the actual result of the numbers, not asserted separately.
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
    league="EPL (demo)",
)

LAKERS_CELTICS = NormalizedEvent(
    sport=Sport.BASKETBALL,
    home_team="Lakers",
    away_team="Celtics",
    commence_time=_now + timedelta(hours=10),
    league="NBA (demo)",
)

CITY_NEWCASTLE = NormalizedEvent(
    sport=Sport.SOCCER,
    home_team="Man City",
    away_team="Newcastle",
    commence_time=_now + timedelta(hours=8),
    league="EPL (demo)",
)


class DemoProvider(OddsProvider):
    name = "demo"

    async def fetch(self, sports: list[Sport]) -> list[OddsQuote]:
        quotes: list[OddsQuote] = []
        quotes += self._arsenal_chelsea()
        quotes += self._lakers_celtics()
        quotes += self._city_newcastle()
        return [q for q in quotes if q.event.sport in sports]

    def _arsenal_chelsea(self) -> list[OddsQuote]:
        e = ARSENAL_CHELSEA
        q = []
        # 3-way moneyline: best home@BookA(2.10), draw@BookB(3.60), away@BookC(4.50)
        # sum(1/2.10 + 1/3.60 + 1/4.50) = 0.9762 -> ~2.4% guaranteed margin.
        for book, home, draw, away in [
            ("DemoBookA", 2.10, 3.40, 4.00),
            ("DemoBookB", 1.95, 3.60, 4.20),
            ("DemoBookC", 2.05, 3.50, 4.50),
        ]:
            q.append(OddsQuote(e, book, MarketType.MONEYLINE_3WAY, "home", home))
            q.append(OddsQuote(e, book, MarketType.MONEYLINE_3WAY, "draw", draw))
            q.append(OddsQuote(e, book, MarketType.MONEYLINE_3WAY, "away", away))

        # Totals 2.5: best over@BookB(2.05), best under@BookC(2.00) -> ~1.2% margin.
        for book, over, under in [
            ("DemoBookA", 1.95, 1.95),
            ("DemoBookB", 2.05, 1.85),
            ("DemoBookC", 1.90, 2.00),
        ]:
            q.append(OddsQuote(e, book, MarketType.TOTALS, "over", over, line=2.5))
            q.append(OddsQuote(e, book, MarketType.TOTALS, "under", under, line=2.5))

        # Asian handicap -0.5 (home): best home@BookB(2.00), best away@BookC(2.05).
        for book, home, away in [
            ("DemoBookA", 1.90, 1.95),
            ("DemoBookB", 2.00, 1.85),
            ("DemoBookC", 1.85, 2.05),
        ]:
            q.append(OddsQuote(e, book, MarketType.ASIAN_HANDICAP, "home", home, line=-0.5))
            q.append(OddsQuote(e, book, MarketType.ASIAN_HANDICAP, "away", away, line=-0.5))

        return q

    def _lakers_celtics(self) -> list[OddsQuote]:
        e = LAKERS_CELTICS
        q = []
        # 2-way moneyline: best home@BookB(2.05), best away@BookA(2.05) -> ~2.4% margin.
        for book, home, away in [
            ("DemoBookA", 1.90, 2.05),
            ("DemoBookB", 2.05, 1.90),
        ]:
            q.append(OddsQuote(e, book, MarketType.MONEYLINE_2WAY, "home", home))
            q.append(OddsQuote(e, book, MarketType.MONEYLINE_2WAY, "away", away))
        return q

    def _city_newcastle(self) -> list[OddsQuote]:
        e = CITY_NEWCASTLE
        q = []
        # A single book's ordinary 1X2 + totals prices (with normal overround,
        # not an arbitrage) used to calibrate the scoreline model...
        q.append(OddsQuote(e, "DemoBookA", MarketType.MONEYLINE_3WAY, "home", 1.50))
        q.append(OddsQuote(e, "DemoBookA", MarketType.MONEYLINE_3WAY, "draw", 4.50))
        q.append(OddsQuote(e, "DemoBookA", MarketType.MONEYLINE_3WAY, "away", 6.50))
        q.append(OddsQuote(e, "DemoBookA", MarketType.TOTALS, "over", 1.90, line=2.5))
        q.append(OddsQuote(e, "DemoBookA", MarketType.TOTALS, "under", 1.95, line=2.5))
        # ...plus a soft book's exotic-market prices. "2-1" is priced well
        # above the model's fair odds (~10.3) so the value-edge scanner has
        # something to find in the local demo; "1-0" and the margin price
        # are near/below fair, so they should NOT be flagged.
        q.append(OddsQuote(e, "DemoBookSoft", MarketType.CORRECT_SCORE, "2-1", 13.00))
        q.append(OddsQuote(e, "DemoBookSoft", MarketType.CORRECT_SCORE, "1-0", 7.00))
        q.append(OddsQuote(e, "DemoBookSoft", MarketType.WINNING_MARGIN, "home_by_1", 3.20))

        # Same-bookmaker cross-line demo (works with Pinnacle alone, no
        # second provider needed): DemoBookA's OWN secondary lines on this
        # match, calibrated against its own 2.5 total above. Only one side
        # per line is quoted here on purpose — with zero-vig "fair" prices,
        # quoting both sides of the same line from the same book would sum
        # to ~1 and could round into looking like a same-book arbitrage,
        # which doesn't happen in reality (see scanner.py) and would be a
        # misleading fixture.
        # Totals 1.5 "under" is priced fair (~4.02) -> should NOT be flagged.
        q.append(OddsQuote(e, "DemoBookA", MarketType.TOTALS, "under", 4.02, line=1.5))
        # Totals 3.5 "over" is priced well above its own fair odds (~3.50) -> SHOULD be flagged.
        q.append(OddsQuote(e, "DemoBookA", MarketType.TOTALS, "over", 4.72, line=3.5))
        # Handicap -1.5 "away" is priced fair (~1.63) -> should NOT be flagged.
        q.append(OddsQuote(e, "DemoBookA", MarketType.ASIAN_HANDICAP, "away", 1.63, line=-1.5))
        return q
