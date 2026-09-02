"""The shared price cache. See planning/MARKET_DATA_DESIGN.md §4.

This is the only thing that reads from a MarketDataSource. The SSE endpoint, trade
execution, and portfolio valuation all read from the cache — never from the source
directly.
"""

import asyncio

from .base import PricePoint


class PriceCache:
    def __init__(self) -> None:
        self._prices: dict[str, PricePoint] = {}
        self._lock = asyncio.Lock()

    async def update(self, batch: list[PricePoint]) -> None:
        async with self._lock:
            for point in batch:
                self._prices[point.ticker] = point

    async def get(self, ticker: str) -> PricePoint | None:
        async with self._lock:
            return self._prices.get(ticker)

    async def snapshot(self) -> dict[str, PricePoint]:
        async with self._lock:
            return dict(self._prices)
