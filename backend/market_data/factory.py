import os

from .base import MarketDataSource
from .massive_source import MassiveSource
from .simulator import SimulatorSource


def get_market_data_source() -> MarketDataSource:
    """Non-empty MASSIVE_API_KEY -> MassiveSource; unset/empty -> SimulatorSource.

    No other env var or config flag controls this (PLAN.md §5).
    """
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if api_key:
        poll_interval = float(os.environ.get("MASSIVE_POLL_INTERVAL_SECONDS", "15"))
        return MassiveSource(api_key=api_key, poll_interval_seconds=poll_interval)
    return SimulatorSource()
