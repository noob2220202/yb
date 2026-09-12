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
