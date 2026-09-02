import asyncio
from datetime import UTC, datetime

import pytest

from market_data.base import PricePoint
from market_data.cache import PriceCache


def make_point(ticker: str, price: float = 100.0, previous: float = 99.0) -> PricePoint:
    return PricePoint(
        ticker=ticker,
        price=price,
        previous_price=previous,
        change=price - previous,
        change_percent=(price - previous) / previous * 100,
        timestamp=datetime.now(UTC),
    )


async def test_get_returns_none_for_unknown_ticker():
    cache = PriceCache()
    assert await cache.get("AAPL") is None


async def test_update_then_get_round_trips():
    cache = PriceCache()
    point = make_point("AAPL")
    await cache.update([point])
    assert await cache.get("AAPL") == point


async def test_update_overwrites_existing_ticker():
    cache = PriceCache()
    await cache.update([make_point("AAPL", price=100.0)])
    second = make_point("AAPL", price=101.0)
    await cache.update([second])
    assert await cache.get("AAPL") == second


async def test_snapshot_returns_all_tickers():
    cache = PriceCache()
    await cache.update([make_point("AAPL"), make_point("GOOGL")])
    snapshot = await cache.snapshot()
    assert set(snapshot.keys()) == {"AAPL", "GOOGL"}


async def test_snapshot_is_a_copy_not_a_live_view():
    cache = PriceCache()
    await cache.update([make_point("AAPL")])
    snapshot = await cache.snapshot()
    await cache.update([make_point("AAPL", price=500.0)])
    assert snapshot["AAPL"].price != 500.0


async def test_concurrent_updates_do_not_corrupt_state():
    cache = PriceCache()
    tickers = [f"T{i}" for i in range(50)]

    async def updater(ticker: str) -> None:
        for _ in range(20):
            await cache.update([make_point(ticker)])
            await asyncio.sleep(0)

    await asyncio.gather(*(updater(t) for t in tickers))
    snapshot = await cache.snapshot()
    assert set(snapshot.keys()) == set(tickers)
    assert all(p.ticker == t for t, p in snapshot.items())
