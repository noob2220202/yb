"""The Odds API (https://the-odds-api.com) adapter.

Aggregates real prices from many independent bookmakers in one call, which
is what makes genuine cross-bookmaker arbitrage possible — Pinnacle alone
is a single price source and can never be arbed against itself. Requires
an API key (``ODDS_API_KEY``); this is a paid third-party product, this
project just consumes its documented v4 REST API.

Parsing is defensive at every nesting level (payload, event, bookmaker,
market, outcome): a malformed or unexpectedly-shaped item is skipped
rather than raising, so one bad record — or an error payload where a
successful list was expected — degrades to "fewer quotes this cycle"
instead of crashing the poll.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from app.core.enums import MarketType, Sport
from app.core.schemas import NormalizedEvent, OddsQuote
from app.providers.base import OddsProvider, parse_decimal_odds, parse_float


def _infer_sport(sport_key: str) -> Sport | None:
    if sport_key.startswith("soccer"):
        return Sport.SOCCER
    if sport_key.startswith("basketball"):
        return Sport.BASKETBALL
    if sport_key.startswith("tennis"):
        return Sport.TENNIS
    return None


def _parse_commence_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_oddsapi_response(payload: object, sport: Sport) -> list[OddsQuote]:
    quotes: list[OddsQuote] = []
    if not isinstance(payload, list):
        return quotes  # e.g. an {"message": "..."} error body instead of the expected list

    for event in payload:
        if not isinstance(event, dict):
            continue
        try:
            quotes.extend(_parse_event(event, sport))
        except Exception:  # noqa: BLE001 - one malformed event must never abort the rest
            continue
    return quotes


def _parse_event(event: dict, sport: Sport) -> list[OddsQuote]:
    home_team = event.get("home_team")
    away_team = event.get("away_team")
    commence_time = _parse_commence_time(event.get("commence_time"))
    if not isinstance(home_team, str) or not isinstance(away_team, str) or commence_time is None:
        return []
    if not home_team or not away_team:
        return []

    league = event.get("sport_title")
    normalized_event = NormalizedEvent(
        sport=sport,
        home_team=home_team,
        away_team=away_team,
        commence_time=commence_time,
        league=league if isinstance(league, str) else "",
    )

    bookmakers = event.get("bookmakers")
    if not isinstance(bookmakers, list):
        return []

    quotes: list[OddsQuote] = []
    for bookmaker in bookmakers:
        if not isinstance(bookmaker, dict):
            continue
        book_name = bookmaker.get("title") or bookmaker.get("key") or "unknown"
        if not isinstance(book_name, str):
            book_name = "unknown"

        markets = bookmaker.get("markets")
        if not isinstance(markets, list):
            continue

        for market in markets:
            if not isinstance(market, dict):
                continue
            try:
                quotes.extend(_parse_market(normalized_event, book_name, home_team, away_team, market))
            except Exception:  # noqa: BLE001 - one malformed market must never abort the rest
                continue

    return quotes


def _parse_market(
    event: NormalizedEvent, book_name: str, home_team: str, away_team: str, market: dict
) -> list[OddsQuote]:
    key = market.get("key")
    outcomes = market.get("outcomes")
    if not isinstance(outcomes, list):
        return []
    outcomes = [o for o in outcomes if isinstance(o, dict)]
    quotes: list[OddsQuote] = []

    if key == "h2h":
        has_draw = any(o.get("name") == "Draw" for o in outcomes)
        market_type = MarketType.MONEYLINE_3WAY if has_draw else MarketType.MONEYLINE_2WAY
        for outcome in outcomes:
            price = parse_decimal_odds(outcome.get("price"))
            if price is None:
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
            price = parse_decimal_odds(outcome.get("price"))
            line = parse_float(outcome.get("point"))
            if price is None or line is None:
                continue
            name = outcome.get("name")
            name = name.lower() if isinstance(name, str) else ""
            if name not in ("over", "under"):
                continue
            quotes.append(OddsQuote(event, book_name, MarketType.TOTALS, name, price, line=line))

    elif key == "spreads":
        home_point = away_point = None
        home_price = away_price = None
        for outcome in outcomes:
            price = parse_decimal_odds(outcome.get("price"))
            point = parse_float(outcome.get("point"))
            if price is None or point is None:
                continue
            if outcome.get("name") == home_team:
                home_point, home_price = point, price
            elif outcome.get("name") == away_team:
                away_point, away_price = point, price
        if (
            home_point is not None
            and away_point is not None
            and home_price is not None
            and away_price is not None
            and abs(home_point + away_point) < 1e-6
        ):
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
                try:
                    quotes.extend(parse_oddsapi_response(payload, inferred))
                except Exception:  # noqa: BLE001 - a parsing bug must never take down the whole poll
                    continue
        return quotes
