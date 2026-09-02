"""The unified market data interface. See planning/MARKET_INTERFACE.md."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class PricePoint:
    ticker: str
    price: float
    previous_price: float
    change: float  # price - previous_price
    change_percent: float  # change / previous_price * 100
    timestamp: datetime


class MarketDataSource(ABC):
    """Produces price updates for a changing set of tickers, forever, until the consuming task is cancelled."""

    @abstractmethod
    def stream(self, get_tickers: Callable[[], set[str]]) -> AsyncIterator[list[PricePoint]]:
        """
        Yield batches of PricePoint updates indefinitely.

        `get_tickers` is called at the start of each cycle to get the current ticker universe
        (watchlist ∪ open positions). Callers don't need to restart the stream when the
        watchlist changes — the callable is re-invoked every cycle.
        """
        ...
