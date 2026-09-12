"""Pinnacle adapter.

Pinnacle publishes an official JSON API (see https://pinnacleapi.github.io/
for current docs and terms) authenticated with HTTP Basic Auth using your
Pinnacle account credentials — this adapter calls that API, it does not
scrape HTML. You need a Pinnacle account with API access approved, and you
must comply with their terms of use; this project takes no position on
your account's eligibility for that.

Field names below follow Pinnacle's documented "get odds" v3 response
shape (leagues -> events -> periods[0] holding money_line/spreads/totals
for the full match). Odds providers evolve their schemas over time, so if
this stops matching, check the current docs and adjust ``_parse_period``
— every parsing step is defensive (skips a malformed record instead of
raising) so a schema drift degrades gracefully rather than crashing polls.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from app.core.enums import MarketType, Sport
from app.core.schemas import NormalizedEvent, OddsQuote
from app.providers.base import OddsProvider

BOOKMAKER_NAME = "Pinnacle"

# Pinnacle's own sport ids are looked up by name at runtime (via /v3/sports)
# rather than hardcoded, since numeric ids aren't guaranteed stable across
# accounts/regions.
_SPORT_NAME_MAP = {
    Sport.SOCCER: "soccer",
    Sport.BASKETBALL: "basketball",
    Sport.TENNIS: "tennis",
}


def parse_pinnacle_odds(payload: dict, sport: Sport) -> list[OddsQuote]:
    quotes: list[OddsQuote] = []
    for league in payload.get("leagues", []) or []:
        league_name = league.get("name", "")
        for event in league.get("events", []) or []:
            try:
                home = event["home"]
                away = event["away"]
                starts = event["starts"]
            except KeyError:
                continue
            try:
                commence_time = datetime.fromisoformat(starts.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                commence_time = datetime.now(timezone.utc)

            normalized_event = NormalizedEvent(
                sport=sport,
                home_team=home,
                away_team=away,
                commence_time=commence_time,
                league=league_name,
            )

            for period in event.get("periods", []) or []:
                if period.get("number") != 0:
                    continue  # only the full-match period, skip halves/quarters
                quotes.extend(_parse_period(normalized_event, period))
    return quotes


def _parse_period(event: NormalizedEvent, period: dict) -> list[OddsQuote]:
    quotes: list[OddsQuote] = []

    money_line = period.get("money_line") or {}
    home_ml, away_ml, draw_ml = money_line.get("home"), money_line.get("away"), money_line.get("draw")
    if home_ml and away_ml:
        market = MarketType.MONEYLINE_3WAY if draw_ml else MarketType.MONEYLINE_2WAY
        quotes.append(OddsQuote(event, BOOKMAKER_NAME, market, "home", float(home_ml)))
        quotes.append(OddsQuote(event, BOOKMAKER_NAME, market, "away", float(away_ml)))
        if draw_ml:
            quotes.append(OddsQuote(event, BOOKMAKER_NAME, market, "draw", float(draw_ml)))

    for spread in period.get("spreads", []) or []:
        try:
            line, home_odds, away_odds = float(spread["hdp"]), float(spread["home"]), float(spread["away"])
        except (KeyError, TypeError, ValueError):
            continue
        quotes.append(OddsQuote(event, BOOKMAKER_NAME, MarketType.ASIAN_HANDICAP, "home", home_odds, line=line))
        quotes.append(OddsQuote(event, BOOKMAKER_NAME, MarketType.ASIAN_HANDICAP, "away", away_odds, line=line))

    for total in period.get("totals", []) or []:
        try:
            line, over_odds, under_odds = float(total["points"]), float(total["over"]), float(total["under"])
        except (KeyError, TypeError, ValueError):
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
        by_name = {s.get("name", "").strip().lower(): s.get("id") for s in payload.get("sports", []) or []}
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
                quotes.extend(parse_pinnacle_odds(payload, sport))
        return quotes
