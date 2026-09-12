"""Pinnacle adapter.

Pinnacle publishes an official RESTful JSON API, authenticated with HTTP
Basic Auth using your Pinnacle account credentials (password capped at 10
characters), documented at
https://github.com/pinnacleapi/pinnacleapi-documentation. This adapter
calls that API — it does not scrape HTML.

**Public API access has been closed since July 23rd, 2025** — new access
requires emailing api@pinnacle.com; an existing account/password alone is
not sufficient anymore. Even with access, note that Pinnacle's API blocks
requests from a long list of jurisdictions (HTTP 451 "unavailable for
legal reasons") — this adapter has never been exercised against a real,
authenticated response for that reason (only against the documented
OpenAPI schema below), so treat the field mapping as "matches the
published contract" rather than "confirmed against live data."

Verified against the official OpenAPI spec (``linesapi-oas.yaml`` in the
docs repo above) rather than guessed:

- ``GET /v2/sports`` -> ``{"sports": [{"id", "name", ...}]}``
- ``GET /v1/fixtures?sportId=`` -> ``{"league": [{"id", "name", "events": [{"id", "home", "away", "starts", ...}]}]}``
  (note the *singular* ``league`` key here, unlike odds below)
- ``GET /v2/odds?sportId=&oddsFormat=Decimal`` ->
  ``{"leagues": [{"id", "events": [{"id", "periods": [{"number", "moneyline", "spreads", "totals", ...}]}]}]}``
  — odds events carry no team names/start time at all, only numeric ids;
  those come from ``/v1/fixtures`` instead and must be joined by event id.

Fair-use rate limits (documented, not merely a courtesy): odds/line
endpoints are limited to 1 request per 2 minutes per sportId, and
``/sports`` to once per 60 minutes. This adapter enforces those itself
(``_ODDS_MIN_INTERVAL`` / ``_FIXTURES_MIN_INTERVAL`` / a one-shot sports
fetch) by reusing the last successful result when called again too soon,
regardless of how short ``POLL_INTERVAL_SECONDS`` is configured — a
misconfigured poll interval should never be able to get an account
throttled or suspended.

Every parsing step is defensive at every nesting level (payload, league,
event, period, individual spread/total row): a malformed or
unexpectedly-shaped item is skipped rather than raising, so one bad
record — or the schema having drifted since this was written — degrades
to "fewer quotes this cycle" instead of crashing the poll.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from app.core.enums import MarketType, Sport
from app.core.schemas import NormalizedEvent, OddsQuote
from app.providers.base import OddsProvider, parse_decimal_odds, parse_float

BOOKMAKER_NAME = "Pinnacle"

_SPORT_NAME_MAP = {
    Sport.SOCCER: "soccer",
    Sport.BASKETBALL: "basketball",
    Sport.TENNIS: "tennis",
}

_SPORTS_MIN_INTERVAL = timedelta(minutes=60)
_FIXTURES_MIN_INTERVAL = timedelta(minutes=2)
_ODDS_MIN_INTERVAL = timedelta(minutes=2)


def _parse_starts(starts: object) -> datetime:
    if isinstance(starts, str):
        try:
            return datetime.fromisoformat(starts.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


class _FixtureInfo:
    __slots__ = ("home", "away", "starts", "league_name")

    def __init__(self, home: str, away: str, starts: datetime, league_name: str):
        self.home = home
        self.away = away
        self.starts = starts
        self.league_name = league_name


def parse_fixtures(payload: object) -> dict[int, _FixtureInfo]:
    """Builds an {event_id: _FixtureInfo} lookup from a ``/v1/fixtures``
    response. Note the top-level key is the *singular* ``league`` here
    (plural ``leagues`` is the odds endpoint's convention) — easy to typo.
    """
    by_event_id: dict[int, _FixtureInfo] = {}
    if not isinstance(payload, dict):
        return by_event_id

    leagues = payload.get("league")
    if not isinstance(leagues, list):
        return by_event_id

    for league in leagues:
        if not isinstance(league, dict):
            continue
        league_name = league.get("name")
        league_name = league_name if isinstance(league_name, str) else ""

        events = league.get("events")
        if not isinstance(events, list):
            continue

        for event in events:
            if not isinstance(event, dict):
                continue
            try:
                event_id = int(event.get("id"))
                home = event["home"]
                away = event["away"]
            except (KeyError, TypeError, ValueError):
                continue
            if not isinstance(home, str) or not isinstance(away, str) or not home or not away:
                continue
            by_event_id[event_id] = _FixtureInfo(
                home=home, away=away, starts=_parse_starts(event.get("starts")), league_name=league_name
            )

    return by_event_id


def parse_pinnacle_odds(payload: object, sport: Sport, fixtures: dict[int, _FixtureInfo]) -> list[OddsQuote]:
    """``fixtures`` is the {event_id: _FixtureInfo} map from
    ``parse_fixtures`` — the odds response alone carries no team names or
    start times, only numeric event ids, so it must be joined against a
    fixtures call for the same sport.
    """
    quotes: list[OddsQuote] = []
    if not isinstance(payload, dict):
        return quotes

    leagues = payload.get("leagues")
    if not isinstance(leagues, list):
        return quotes

    for league in leagues:
        if not isinstance(league, dict):
            continue
        events = league.get("events")
        if not isinstance(events, list):
            continue

        for event in events:
            if not isinstance(event, dict):
                continue
            try:
                event_id = int(event.get("id"))
            except (TypeError, ValueError):
                continue
            fixture = fixtures.get(event_id)
            if fixture is None:
                continue  # odds for an event we have no fixture (team names) for -- can't normalize it

            normalized_event = NormalizedEvent(
                sport=sport,
                home_team=fixture.home,
                away_team=fixture.away,
                commence_time=fixture.starts,
                league=fixture.league_name,
            )

            periods = event.get("periods")
            if not isinstance(periods, list):
                continue
            for period in periods:
                if not isinstance(period, dict) or period.get("number") != 0:
                    continue  # only the full-match period, skip halves/quarters
                try:
                    quotes.extend(_parse_period(normalized_event, period))
                except Exception:  # noqa: BLE001 - one malformed period must never abort the rest
                    continue

    return quotes


def _parse_period(event: NormalizedEvent, period: dict) -> list[OddsQuote]:
    quotes: list[OddsQuote] = []

    moneyline = period.get("moneyline")
    if isinstance(moneyline, dict):
        home_odds = parse_decimal_odds(moneyline.get("home"))
        away_odds = parse_decimal_odds(moneyline.get("away"))
        draw_odds = parse_decimal_odds(moneyline.get("draw"))
        if home_odds is not None and away_odds is not None:
            market = MarketType.MONEYLINE_3WAY if draw_odds is not None else MarketType.MONEYLINE_2WAY
            quotes.append(OddsQuote(event, BOOKMAKER_NAME, market, "home", home_odds))
            quotes.append(OddsQuote(event, BOOKMAKER_NAME, market, "away", away_odds))
            if draw_odds is not None:
                quotes.append(OddsQuote(event, BOOKMAKER_NAME, market, "draw", draw_odds))

    spreads = period.get("spreads")
    if isinstance(spreads, list):
        for spread in spreads:
            if not isinstance(spread, dict):
                continue
            line = parse_float(spread.get("hdp"))
            home_odds = parse_decimal_odds(spread.get("home"))
            away_odds = parse_decimal_odds(spread.get("away"))
            if line is None or home_odds is None or away_odds is None:
                continue
            quotes.append(OddsQuote(event, BOOKMAKER_NAME, MarketType.ASIAN_HANDICAP, "home", home_odds, line=line))
            quotes.append(OddsQuote(event, BOOKMAKER_NAME, MarketType.ASIAN_HANDICAP, "away", away_odds, line=line))

    totals = period.get("totals")
    if isinstance(totals, list):
        for total in totals:
            if not isinstance(total, dict):
                continue
            line = parse_float(total.get("points"))
            over_odds = parse_decimal_odds(total.get("over"))
            under_odds = parse_decimal_odds(total.get("under"))
            if line is None or over_odds is None or under_odds is None:
                continue
            quotes.append(OddsQuote(event, BOOKMAKER_NAME, MarketType.TOTALS, "over", over_odds, line=line))
            quotes.append(OddsQuote(event, BOOKMAKER_NAME, MarketType.TOTALS, "under", under_odds, line=line))

    return quotes


class PinnacleProvider(OddsProvider):
    name = "pinnacle"

    def __init__(self, base_url: str, username: str, password: str, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._transport = transport  # test-only hook (httpx.MockTransport); None -> real network
        self._sport_ids: dict[Sport, int] = {}
        self._last_sports_fetch: datetime | None = None
        self._last_fixtures_fetch: dict[int, datetime] = {}
        self._last_odds_fetch: dict[int, datetime] = {}
        self._cached_quotes: dict[int, list[OddsQuote]] = {}

    @property
    def is_configured(self) -> bool:
        return bool(self.username and self.password)

    async def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            auth=(self.username, self.password),
            timeout=15.0,
            transport=self._transport,
        )

    async def _resolve_sport_ids(self, client: httpx.AsyncClient) -> dict[Sport, int]:
        now = datetime.now(timezone.utc)
        if self._sport_ids and self._last_sports_fetch is not None:
            return self._sport_ids  # fetched once ever is enough; refetching is rarely needed and rate-limited to 1/hour
        if self._last_sports_fetch is not None and now - self._last_sports_fetch < _SPORTS_MIN_INTERVAL:
            return self._sport_ids

        try:
            resp = await client.get("/v2/sports")
            resp.raise_for_status()
            payload = resp.json()
        except httpx.HTTPError:
            return self._sport_ids
        finally:
            self._last_sports_fetch = now

        if not isinstance(payload, dict):
            return self._sport_ids
        sports = payload.get("sports")
        if not isinstance(sports, list):
            return self._sport_ids

        by_name: dict[str, int] = {}
        for entry in sports:
            if not isinstance(entry, dict):
                continue
            entry_name = entry.get("name")
            entry_id = entry.get("id")
            if isinstance(entry_name, str) and isinstance(entry_id, int):
                by_name[entry_name.strip().lower()] = entry_id

        for sport, name in _SPORT_NAME_MAP.items():
            sport_id = by_name.get(name)
            if sport_id is not None:
                self._sport_ids[sport] = sport_id
        return self._sport_ids

    async def _fetch_fixtures(self, client: httpx.AsyncClient, sport_id: int) -> dict[int, _FixtureInfo]:
        now = datetime.now(timezone.utc)
        last = self._last_fixtures_fetch.get(sport_id)
        if last is not None and now - last < _FIXTURES_MIN_INTERVAL:
            return {}  # too soon per fair-use policy; odds for unmatched events are simply skipped this cycle
        self._last_fixtures_fetch[sport_id] = now
        try:
            resp = await client.get("/v1/fixtures", params={"sportId": sport_id})
            resp.raise_for_status()
            payload = resp.json()
        except httpx.HTTPError:
            return {}
        try:
            return parse_fixtures(payload)
        except Exception:  # noqa: BLE001 - a parsing bug must never take down the whole poll
            return {}

    async def fetch(self, sports: list[Sport]) -> list[OddsQuote]:
        if not self.is_configured:
            return []
        quotes: list[OddsQuote] = []
        async with await self._client() as client:
            sport_ids = await self._resolve_sport_ids(client)
            for sport in sports:
                sport_id = sport_ids.get(sport)
                if sport_id is None:
                    continue

                now = datetime.now(timezone.utc)
                last_odds = self._last_odds_fetch.get(sport_id)
                if last_odds is not None and now - last_odds < _ODDS_MIN_INTERVAL:
                    # Rate-limited (1 request / 2 min / sportId) — reuse the
                    # last successful fetch instead of skipping to empty, so
                    # a short POLL_INTERVAL_SECONDS doesn't make opportunities
                    # spuriously flicker in and out between polls.
                    quotes.extend(self._cached_quotes.get(sport_id, []))
                    continue

                fixtures = await self._fetch_fixtures(client, sport_id)
                if not fixtures:
                    quotes.extend(self._cached_quotes.get(sport_id, []))
                    continue

                self._last_odds_fetch[sport_id] = now
                try:
                    resp = await client.get(
                        "/v2/odds", params={"sportId": sport_id, "oddsFormat": "Decimal"}
                    )
                    resp.raise_for_status()
                    payload = resp.json()
                except httpx.HTTPError:
                    quotes.extend(self._cached_quotes.get(sport_id, []))
                    continue

                try:
                    sport_quotes = parse_pinnacle_odds(payload, sport, fixtures)
                except Exception:  # noqa: BLE001 - a parsing bug must never take down the whole poll
                    sport_quotes = []

                self._cached_quotes[sport_id] = sport_quotes
                quotes.extend(sport_quotes)
        return quotes
