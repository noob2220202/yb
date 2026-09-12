from __future__ import annotations

from abc import ABC, abstractmethod

from app.core.enums import Sport
from app.core.schemas import OddsQuote


class OddsProvider(ABC):
    """Adapter that turns one external odds source into canonical
    ``OddsQuote`` objects. Implementations must never raise on a single
    malformed item — skip it and keep going, so one bad record from an
    upstream API doesn't take down a whole poll cycle.
    """

    name: str

    @abstractmethod
    async def fetch(self, sports: list[Sport]) -> list[OddsQuote]: ...

    @property
    def is_configured(self) -> bool:
        """Whether this provider has the credentials/config it needs to run.
        Providers that aren't configured are skipped by the scheduler rather
        than erroring on every poll.
        """
        return True


def parse_float(value: object) -> float | None:
    """Safely converts an arbitrary JSON value to ``float``, returning
    ``None`` instead of raising for anything that isn't a valid number
    (missing field, ``None``, a non-numeric string, a nested dict/list from
    an unexpectedly-shaped response, ...). Shared by every provider adapter
    so line values are validated the same way everywhere.
    """
    if isinstance(value, bool):  # bool is an int subclass; never a real line/odds value
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def parse_decimal_odds(value: object) -> float | None:
    """Like ``parse_float``, but also rejects anything that isn't a valid
    decimal price: odds must be > 1.0 (an odds of 1.0 or below implies zero
    or negative payout and is never a real quote — almost always a sign of
    bad data or a different odds format leaking through).
    """
    odds = parse_float(value)
    if odds is None or odds <= 1.0:
        return None
    return odds
