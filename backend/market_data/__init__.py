"""Unified market data layer: a single abstract source interface, a shared
in-memory cache, and two implementations (simulator, Massive) selected at
startup via `get_market_data_source()`. See planning/MARKET_DATA_DESIGN.md.
"""

from .base import MarketDataSource, PricePoint
from .cache import PriceCache
from .factory import get_market_data_source
from .massive_source import MassiveSource
from .simulator import DEFAULT_TICKERS, SimulatorSource, TickerConfig

__all__ = [
    "DEFAULT_TICKERS",
    "MarketDataSource",
    "MassiveSource",
    "PriceCache",
    "PricePoint",
    "SimulatorSource",
    "TickerConfig",
    "get_market_data_source",
]
