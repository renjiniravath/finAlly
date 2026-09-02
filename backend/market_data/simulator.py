import asyncio
import math
import random
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from .base import MarketDataSource, PricePoint

TICK_SECONDS = 0.5
TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600
SIMULATED_SECONDS_PER_TICK = 5 * 60
DT_YEARS = SIMULATED_SECONDS_PER_TICK / TRADING_SECONDS_PER_YEAR

W_MARKET = 0.5
W_SECTOR = 0.5
W_IDIO = math.sqrt(1 - W_MARKET**2 - W_SECTOR**2)

EVENT_PROBABILITY_PER_TICK = 0.0005
EVENT_MAGNITUDE_RANGE = (0.02, 0.05)


@dataclass(frozen=True, slots=True)
class TickerConfig:
    ticker: str
    seed_price: float
    annual_drift: float
    annual_volatility: float
    sector: str


DEFAULT_TICKERS: list[TickerConfig] = [
    TickerConfig("AAPL", 190.0, 0.08, 0.28, "tech"),
    TickerConfig("GOOGL", 175.0, 0.08, 0.30, "tech"),
    TickerConfig("MSFT", 420.0, 0.08, 0.26, "tech"),
    TickerConfig("AMZN", 185.0, 0.10, 0.32, "tech"),
    TickerConfig("NVDA", 130.0, 0.15, 0.45, "tech"),
    TickerConfig("META", 500.0, 0.10, 0.35, "tech"),
    TickerConfig("TSLA", 250.0, 0.05, 0.55, "auto"),
    TickerConfig("JPM", 210.0, 0.06, 0.22, "financial"),
    TickerConfig("V", 280.0, 0.07, 0.20, "financial"),
    TickerConfig("NFLX", 650.0, 0.09, 0.33, "media"),
]


class SimulatorSource(MarketDataSource):
    """Default MarketDataSource: geometric Brownian motion with a 3-factor
    (market/sector/idiosyncratic) correlation model and occasional event jumps."""

    def __init__(self, configs: list[TickerConfig] = DEFAULT_TICKERS, seed: int | None = None) -> None:
        self._configs: dict[str, TickerConfig] = {c.ticker: c for c in configs}
        self._prices: dict[str, float] = {c.ticker: c.seed_price for c in configs}
        self._rng = random.Random(seed)

    async def stream(self, get_tickers: Callable[[], set[str]]) -> AsyncIterator[list[PricePoint]]:
        while True:
            yield self._tick(get_tickers())
            await asyncio.sleep(TICK_SECONDS)

    def _tick(self, tickers: set[str]) -> list[PricePoint]:
        for ticker in tickers:
            self._configs.setdefault(ticker, self._synthesize_config(ticker))
            self._prices.setdefault(ticker, self._configs[ticker].seed_price)

        sectors = {self._configs[t].sector for t in tickers}
        z_market = self._rng.gauss(0, 1)
        z_sector = {s: self._rng.gauss(0, 1) for s in sectors}

        now = datetime.now(UTC)
        points = []
        for ticker in tickers:
            config = self._configs[ticker]
            previous = self._prices[ticker]

            z_idio = self._rng.gauss(0, 1)
            shock = W_MARKET * z_market + W_SECTOR * z_sector[config.sector] + W_IDIO * z_idio
            drift = (config.annual_drift - 0.5 * config.annual_volatility**2) * DT_YEARS
            diffusion = config.annual_volatility * math.sqrt(DT_YEARS) * shock
            price = previous * math.exp(drift + diffusion)

            if self._rng.random() < EVENT_PROBABILITY_PER_TICK:
                magnitude = self._rng.uniform(*EVENT_MAGNITUDE_RANGE)
                sign = self._rng.choice((-1, 1))
                price *= 1 + sign * magnitude

            self._prices[ticker] = price
            points.append(
                PricePoint(
                    ticker=ticker,
                    price=price,
                    previous_price=previous,
                    change=price - previous,
                    change_percent=(price - previous) / previous * 100,
                    timestamp=now,
                )
            )
        return points

    @staticmethod
    def _synthesize_config(ticker: str) -> TickerConfig:
        """A ticker added at runtime that isn't in DEFAULT_TICKERS gets generic parameters and
        its own standalone sector (equal to its ticker), so it doesn't spuriously correlate with
        an unrelated group."""
        return TickerConfig(ticker, seed_price=100.0, annual_drift=0.08, annual_volatility=0.30, sector=ticker)
