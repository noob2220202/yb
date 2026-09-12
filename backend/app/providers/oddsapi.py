"""The Odds API (https://the-odds-api.com) adapter.

Aggregates real prices from many independent bookmakers in one call, which
is what makes genuine cross-bookmaker arbitrage possible — Pinnacle alone
is a single price source and can never be arbed against itself. Requires
an API key (``ODDS_API_KEY``); this is a paid third-party product, this
project just consumes its documented v4 REST API.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from app.core.enums import MarketType, Sport
from app.core.schemas import NormalizedEvent, OddsQuote
from app.providers.base import OddsProvider


def _infer_sport(sport_key: str) -> Sport | None:
    if sport_key.startswith("soccer"):
        return Sport.SOCCER
    if sport_key.startswith("basketball"):
        return Sport.BASKETBALL
    if sport_key.startswith("tennis"):
        return Sport.TENNIS
    return None


def parse_oddsapi_response(payload: list[dict], sport: Sport) -> list[OddsQuote]:
    quotes: list[OddsQuote] = []
    for event in payload or []:
        try:
            home_team = event["home_team"]
            away_team = event["away_team"]
            commence_time = datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00"))
        except (KeyError, ValueError, AttributeError):
            continue

        normalized_event = NormalizedEvent(
            sport=sport,
            home_team=home_team,
            away_team=away_team,
            commence_time=commence_time,
            league=event.get("sport_title", ""),
        )

        for bookmaker in event.get("bookmakers", []) or []:
            book_name = bookmaker.get("title") or bookmaker.get("key") or "unknown"
            for market in bookmaker.get("markets", []) or []:
                quotes.extend(_parse_market(normalized_event, book_name, home_team, away_team, market))
    return quotes


def _parse_market(
    event: NormalizedEvent, book_name: str, home_team: str, away_team: str, market: dict
) -> list[OddsQuote]:
    key = market.get("key")
    outcomes = market.get("outcomes", []) or []
    quotes: list[OddsQuote] = []

    if key == "h2h":
        has_draw = any(o.get("name") == "Draw" for o in outcomes)
        market_type = MarketType.MONEYLINE_3WAY if has_draw else MarketType.MONEYLINE_2WAY
        for outcome in outcomes:
            try:
                price = float(outcome["price"])
            except (KeyError, TypeError, ValueError):
                continue
            name = outcome.get("name")
            if name == home_team:
                selection = "home"
            elif name == away_team:
                selection = "away"
            elif name == "Draw":
                selection = "draw"
            else:
                continue
            quotes.append(OddsQuote(event, book_name, market_type, selection, price))

    elif key == "totals":
        for outcome in outcomes:
            try:
                price = float(outcome["price"])
                line = float(outcome["point"])
            except (KeyError, TypeError, ValueError):
                continue
            name = (outcome.get("name") or "").lower()
            if name not in ("over", "under"):
                continue
            quotes.append(OddsQuote(event, book_name, MarketType.TOTALS, name, price, line=line))

    elif key == "spreads":
        home_point = None
        away_point = None
        home_price = away_price = None
        for outcome in outcomes:
            try:
                price = float(outcome["price"])
                point = float(outcome["point"])
            except (KeyError, TypeError, ValueError):
                continue
            if outcome.get("name") == home_team:
                home_point, home_price = point, price
            elif outcome.get("name") == away_team:
                away_point, away_price = point, price
        if home_point is not None and away_point is not None and abs(home_point + away_point) < 1e-6:
            quotes.append(OddsQuote(event, book_name, MarketType.ASIAN_HANDICAP, "home", home_price, line=home_point))
            quotes.append(OddsQuote(event, book_name, MarketType.ASIAN_HANDICAP, "away", away_price, line=home_point))

    return quotes


class OddsApiProvider(OddsProvider):
    name = "oddsapi"

    def __init__(self, base_url: str, api_key: str, sport_keys: list[str]):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.sport_keys = sport_keys

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def fetch(self, sports: list[Sport]) -> list[OddsQuote]:
        if not self.is_configured:
            return []
        quotes: list[OddsQuote] = []
        async with httpx.AsyncClient(base_url=self.base_url, timeout=15.0) as client:
            for sport_key in self.sport_keys:
                inferred = _infer_sport(sport_key)
                if inferred is None or inferred not in sports:
                    continue
                try:
                    resp = await client.get(
                        f"/v4/sports/{sport_key}/odds",
                        params={
                            "apiKey": self.api_key,
                            "regions": "us,uk,eu",
                            "markets": "h2h,spreads,totals",
                            "oddsFormat": "decimal",
                        },
                    )
                    resp.raise_for_status()
                    payload = resp.json()
                except httpx.HTTPError:
                    continue
                quotes.extend(parse_oddsapi_response(payload, inferred))
        return quotes
