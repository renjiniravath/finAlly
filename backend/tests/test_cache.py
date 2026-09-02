import asyncio
from datetime import UTC, datetime

import pytest

from market_data.base import PricePoint
from market_data.cache import PriceCache


def make_point(ticker: str, price: float, previous: float) -> PricePoint:
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
    point = make_point("AAPL", 191.0, 190.0)
    await cache.update([point])
    assert await cache.get("AAPL") == point


async def test_update_overwrites_existing_ticker():
    cache = PriceCache()
    first = make_point("AAPL", 191.0, 190.0)
    second = make_point("AAPL", 192.0, 191.0)
    await cache.update([first])
    await cache.update([second])
    assert await cache.get("AAPL") == second


async def test_update_batch_sets_multiple_tickers():
    cache = PriceCache()
    aapl = make_point("AAPL", 191.0, 190.0)
    googl = make_point("GOOGL", 176.0, 175.0)
    await cache.update([aapl, googl])
    snapshot = await cache.snapshot()
    assert snapshot == {"AAPL": aapl, "GOOGL": googl}


async def test_snapshot_is_a_copy_not_a_live_view():
    cache = PriceCache()
    await cache.update([make_point("AAPL", 191.0, 190.0)])
    snapshot = await cache.snapshot()

    await cache.update([make_point("AAPL", 200.0, 191.0)])

    assert snapshot["AAPL"].price == 191.0
    assert (await cache.get("AAPL")).price == 200.0


async def test_snapshot_mutation_does_not_affect_cache():
    cache = PriceCache()
    await cache.update([make_point("AAPL", 191.0, 190.0)])
    snapshot = await cache.snapshot()
    snapshot["GOOGL"] = make_point("GOOGL", 1.0, 1.0)

    assert await cache.get("GOOGL") is None


async def test_concurrent_updates_do_not_corrupt_state():
    cache = PriceCache()
    tickers = [f"T{i}" for i in range(20)]

    async def writer(ticker: str) -> None:
        for step in range(50):
            await cache.update([make_point(ticker, 100.0 + step, 100.0)])

    await asyncio.gather(*(writer(t) for t in tickers))

    snapshot = await cache.snapshot()
    assert set(snapshot.keys()) == set(tickers)
    for ticker in tickers:
        assert snapshot[ticker].price == 149.0
