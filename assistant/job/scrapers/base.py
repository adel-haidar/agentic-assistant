import asyncio
from abc import ABC, abstractmethod
from typing import Optional

from assistant.job.models import JobListing


class BaseScraper(ABC):
    name: str = "base"

    def __init__(self, delay_seconds: float = 2.0, max_per_query: int = 20):
        self._delay = delay_seconds
        self._max = max_per_query

    @abstractmethod
    async def search(
        self, query: str, country: str, city: Optional[str] = None
    ) -> list[JobListing]: ...

    async def _sleep(self) -> None:
        await asyncio.sleep(self._delay)
