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

Markets requested: core (``h2h``, ``spreads``, ``totals``) and, in a
SEPARATE request per sport, the "additional markets" ``btts`` (both
teams to score) and ``correct_score`` — both verified as real market
keys against The Odds API's own docs. They're split into their own
request specifically so a plan that doesn't include additional markets
(a 4xx on that call) can never cost the core markets the arbitrage
engine depends on. ``correct_score``'s outcome-name format has NOT been
verified against a live paid response (see README "마켓 커버리지"); its
parser extracts the two score digits from the outcome name and assumes
"home-away" order, skipping anything that doesn't yield exactly two
digit groups (e.g. an "Any Other Score" catch-all) rather than
guessing. ``winning_margin`` has no known market key on this API and is
intentionally not requested.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import httpx

from app.core.enums import MarketType, Sport
from app.core.schemas import NormalizedEvent, OddsQuote
from app.providers.base import OddsProvider, parse_decimal_odds, parse_float

_CORRECT_SCORE_DIGITS = re.compile(r"\d+")


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

    elif key == "btts":
        for outcome in outcomes:
            price = parse_decimal_odds(outcome.get("price"))
            if price is None:
                continue
            name = outcome.get("name")
            name = name.lower() if isinstance(name, str) else ""
            if name not in ("yes", "no"):
                continue
            quotes.append(OddsQuote(event, book_name, MarketType.BOTH_TEAMS_TO_SCORE, name, price))

    elif key == "correct_score":
        # Best-effort: unverified against a live paid response (see
        # app/providers/oddsapi.py module docstring / README "마켓
        # 커버리지") — extracts the two score digits from the outcome
        # name (whatever surrounding text/separator it uses) and maps
        # them to this project's "H-A" selection convention. Anything
        # that isn't exactly two digit groups (e.g. an "Any Other
        # Score" catch-all bucket) is skipped rather than guessed at.
        for outcome in outcomes:
            price = parse_decimal_odds(outcome.get("price"))
            name = outcome.get("name")
            if price is None or not isinstance(name, str):
                continue
            digits = _CORRECT_SCORE_DIGITS.findall(name)
            if len(digits) != 2:
                continue
            quotes.append(OddsQuote(event, book_name, MarketType.CORRECT_SCORE, f"{digits[0]}-{digits[1]}", price))

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

    def __init__(
        self,
        base_url: str,
        api_key: str,
        sport_keys: list[str],
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.sport_keys = sport_keys
        self._transport = transport  # test-only hook (httpx.MockTransport); None -> real network

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def _fetch_markets(self, client: httpx.AsyncClient, sport_key: str, markets: str) -> object | None:
        try:
            resp = await client.get(
                f"/v4/sports/{sport_key}/odds",
                params={
                    "apiKey": self.api_key,
                    "regions": "us,uk,eu",
                    "markets": markets,
                    "oddsFormat": "decimal",
                },
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError:
            return None

    async def fetch(self, sports: list[Sport]) -> list[OddsQuote]:
        if not self.is_configured:
            return []
        quotes: list[OddsQuote] = []
        async with httpx.AsyncClient(base_url=self.base_url, timeout=15.0, transport=self._transport) as client:
            for sport_key in self.sport_keys:
                inferred = _infer_sport(sport_key)
                if inferred is None or inferred not in sports:
                    continue

                core_payload = await self._fetch_markets(client, sport_key, "h2h,spreads,totals")
                if core_payload is not None:
                    try:
                        quotes.extend(parse_oddsapi_response(core_payload, inferred))
                    except Exception:  # noqa: BLE001 - a parsing bug must never take down the whole poll
                        pass

                # Separate request/plan-quota boundary from the core
                # markets above on purpose: btts/correct_score are
                # "additional markets" that may not be included in every
                # plan, and a rejected/unsupported request for them must
                # never risk losing the core moneyline/spreads/totals
                # quotes the arbitrage engine depends on.
                exotic_payload = await self._fetch_markets(client, sport_key, "btts,correct_score")
                if exotic_payload is not None:
                    try:
                        quotes.extend(parse_oddsapi_response(exotic_payload, inferred))
                    except Exception:  # noqa: BLE001 - a parsing bug must never take down the whole poll
                        pass
        return quotes
