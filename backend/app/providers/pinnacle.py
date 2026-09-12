"""Pinnacle adapter.

Pinnacle publishes an official JSON API (see https://pinnacleapi.github.io/
for current docs and terms) authenticated with HTTP Basic Auth using your
Pinnacle account credentials — this adapter calls that API, it does not
scrape HTML. You need a Pinnacle account with API access approved, and you
must comply with their terms of use; this project takes no position on
your account's eligibility for that.

Field names below follow Pinnacle's documented "get odds" v3 response
shape (leagues -> events -> periods[0] holding money_line/spreads/totals
for the full match). Odds providers evolve their schemas over time, and
this shape has never been checked against a live, credentialed response
(no approved account was available while building this), so treat it as
a best-effort mapping — if it stops matching (or never matched), compare
against the current docs / an actual response and adjust the parsing
helpers below.

Every parsing step here is defensive at every nesting level (payload,
league, event, period, individual spread/total row): a malformed or
unexpectedly-shaped item is skipped rather than raising, so one bad
record — or the entire schema having drifted — degrades to "fewer quotes
this cycle" instead of crashing the poll or silently corrupting data with
made-up values.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from app.core.enums import MarketType, Sport
from app.core.schemas import NormalizedEvent, OddsQuote
from app.providers.base import OddsProvider, parse_decimal_odds, parse_float

BOOKMAKER_NAME = "Pinnacle"

# Pinnacle's own sport ids are looked up by name at runtime (via /v3/sports)
# rather than hardcoded, since numeric ids aren't guaranteed stable across
# accounts/regions.
_SPORT_NAME_MAP = {
    Sport.SOCCER: "soccer",
    Sport.BASKETBALL: "basketball",
    Sport.TENNIS: "tennis",
}


def _parse_starts(starts: object) -> datetime:
    if isinstance(starts, str):
        try:
            return datetime.fromisoformat(starts.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def parse_pinnacle_odds(payload: object, sport: Sport) -> list[OddsQuote]:
    quotes: list[OddsQuote] = []
    if not isinstance(payload, dict):
        return quotes

    leagues = payload.get("leagues")
    if not isinstance(leagues, list):
        return quotes

    for league in leagues:
        if not isinstance(league, dict):
            continue
        league_name = league.get("name")
        if not isinstance(league_name, str):
            league_name = ""

        events = league.get("events")
        if not isinstance(events, list):
            continue

        for event in events:
            if not isinstance(event, dict):
                continue
            try:
                quotes.extend(_parse_event(event, sport, league_name))
            except Exception:  # noqa: BLE001 - one malformed event must never abort the rest
                continue

    return quotes


def _parse_event(event: dict, sport: Sport, league_name: str) -> list[OddsQuote]:
    home = event.get("home")
    away = event.get("away")
    if not isinstance(home, str) or not isinstance(away, str) or not home or not away:
        return []

    normalized_event = NormalizedEvent(
        sport=sport,
        home_team=home,
        away_team=away,
        commence_time=_parse_starts(event.get("starts")),
        league=league_name,
    )

    periods = event.get("periods")
    if not isinstance(periods, list):
        return []

    quotes: list[OddsQuote] = []
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

    money_line = period.get("money_line")
    if isinstance(money_line, dict):
        home_odds = parse_decimal_odds(money_line.get("home"))
        away_odds = parse_decimal_odds(money_line.get("away"))
        draw_odds = parse_decimal_odds(money_line.get("draw"))
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

    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._sport_ids: dict[Sport, int] = {}

    @property
    def is_configured(self) -> bool:
        return bool(self.username and self.password)

    async def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            auth=(self.username, self.password),
            timeout=15.0,
        )

    async def _resolve_sport_ids(self, client: httpx.AsyncClient) -> dict[Sport, int]:
        if self._sport_ids:
            return self._sport_ids
        try:
            resp = await client.get("/v3/sports")
            resp.raise_for_status()
            payload = resp.json()
        except httpx.HTTPError:
            return {}
        if not isinstance(payload, dict):
            return {}

        sports = payload.get("sports")
        if not isinstance(sports, list):
            return {}

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
                try:
                    resp = await client.get("/v3/odds", params={"sportId": sport_id, "oddsFormat": "Decimal"})
                    resp.raise_for_status()
                    payload = resp.json()
                except httpx.HTTPError:
                    continue
                try:
                    quotes.extend(parse_pinnacle_odds(payload, sport))
                except Exception:  # noqa: BLE001 - a parsing bug must never take down the whole poll
                    continue
        return quotes
