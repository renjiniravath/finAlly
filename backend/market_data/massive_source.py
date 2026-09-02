import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime

from massive import RESTClient
from massive.exceptions import BadResponse

from .base import MarketDataSource, PricePoint

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_SECONDS = 15.0


class MassiveSource(MarketDataSource):
    """MarketDataSource backed by the Massive (Polygon.io) REST snapshot endpoint. Used when
    MASSIVE_API_KEY is configured. Polls the current ticker universe on a fixed interval — one
    request covers every ticker, keeping the free tier's 5 req/min limit comfortably safe."""

    def __init__(self, api_key: str, poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS) -> None:
        self._client = RESTClient(api_key=api_key)
        self._poll_interval = poll_interval_seconds

    async def stream(self, get_tickers: Callable[[], set[str]]) -> AsyncIterator[list[PricePoint]]:
        while True:
            tickers = get_tickers()
            if tickers:
                try:
                    yield await asyncio.to_thread(self._fetch, tickers)
                except BadResponse:
                    logger.exception("massive snapshot fetch failed, keeping last known prices")
            await asyncio.sleep(self._poll_interval)

    def _fetch(self, tickers: set[str]) -> list[PricePoint]:
        snapshots = self._client.get_snapshot_all(market_type="stocks", tickers=list(tickers))
        now = datetime.now(UTC)
        points = []
        for s in snapshots:
            if s.day is None or s.prev_day is None or s.day.close is None:
                continue  # pre-market: no trades yet today, keep last known price in the cache
            price = s.day.close
            previous = s.prev_day.close
            points.append(
                PricePoint(
                    ticker=s.ticker,
                    price=price,
                    previous_price=previous,
                    change=s.todays_change if s.todays_change is not None else price - previous,
                    change_percent=(
                        s.todays_change_percent
                        if s.todays_change_percent is not None
                        else (price - previous) / previous * 100
                    ),
                    timestamp=now,
                )
            )
        return points
