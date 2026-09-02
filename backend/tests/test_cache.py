import asyncio
from datetime import UTC, datetime

import pytest

from market_data.base import PricePoint
from market_data.cache import PriceCache


def make_point(ticker: str, price: float) -> PricePoint:
    return PricePoint(
        ticker=ticker,
        price=price,
        previous_price=price - 1,
        change=1.0,
        change_percent=1.0,
        timestamp=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_get_missing_ticker_returns_none():
    cache = PriceCache()
    assert await cache.get("AAPL") is None


@pytest.mark.asyncio
async def test_update_then_get_round_trips():
    cache = PriceCache()
    point = make_point("AAPL", 101.0)
    await cache.update([point])
    assert await cache.get("AAPL") == point


@pytest.mark.asyncio
async def test_update_overwrites_existing_ticker():
    cache = PriceCache()
    await cache.update([make_point("AAPL", 101.0)])
    second = make_point("AAPL", 102.0)
    await cache.update([second])
    assert await cache.get("AAPL") == second


@pytest.mark.asyncio
async def test_snapshot_returns_all_tickers():
    cache = PriceCache()
    await cache.update([make_point("AAPL", 101.0), make_point("GOOGL", 175.0)])
    snapshot = await cache.snapshot()
    assert set(snapshot.keys()) == {"AAPL", "GOOGL"}


@pytest.mark.asyncio
async def test_snapshot_is_a_copy_not_a_live_view():
    cache = PriceCache()
    await cache.update([make_point("AAPL", 101.0)])
    snapshot = await cache.snapshot()
    await cache.update([make_point("AAPL", 999.0)])
    assert snapshot["AAPL"].price == 101.0
    assert (await cache.get("AAPL")).price == 999.0


@pytest.mark.asyncio
async def test_concurrent_updates_do_not_corrupt_state():
    cache = PriceCache()

    async def updater(ticker: str, n: int) -> None:
        for i in range(n):
            await cache.update([make_point(ticker, float(i))])

    await asyncio.gather(*(updater(f"T{i}", 50) for i in range(10)))
    snapshot = await cache.snapshot()
    assert len(snapshot) == 10
    for i in range(10):
        assert snapshot[f"T{i}"].price == 49.0
