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


async def test_get_returns_none_for_unknown_ticker():
    cache = PriceCache()
    assert await cache.get("AAPL") is None


async def test_update_then_get_round_trips():
    cache = PriceCache()
    point = make_point("AAPL", 190.0)
    await cache.update([point])
    assert await cache.get("AAPL") == point


async def test_update_overwrites_existing_ticker():
    cache = PriceCache()
    await cache.update([make_point("AAPL", 190.0)])
    second = make_point("AAPL", 191.0)
    await cache.update([second])
    assert await cache.get("AAPL") == second


async def test_snapshot_returns_all_tickers():
    cache = PriceCache()
    await cache.update([make_point("AAPL", 190.0), make_point("GOOGL", 175.0)])
    snapshot = await cache.snapshot()
    assert set(snapshot) == {"AAPL", "GOOGL"}


async def test_snapshot_is_a_copy_not_a_live_view():
    cache = PriceCache()
    await cache.update([make_point("AAPL", 190.0)])
    snapshot = await cache.snapshot()
    await cache.update([make_point("AAPL", 999.0)])
    assert snapshot["AAPL"].price == 190.0
    assert (await cache.get("AAPL")).price == 999.0


async def test_concurrent_updates_do_not_corrupt_state():
    cache = PriceCache()
    tickers = [f"T{i}" for i in range(50)]

    async def updater(ticker: str) -> None:
        for price in range(1, 21):
            await cache.update([make_point(ticker, float(price))])

    await asyncio.gather(*(updater(t) for t in tickers))

    snapshot = await cache.snapshot()
    assert set(snapshot) == set(tickers)
    for ticker in tickers:
        assert snapshot[ticker].price == 20.0
