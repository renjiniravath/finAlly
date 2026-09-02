import asyncio
from datetime import UTC, datetime

from market_data.base import PricePoint
from market_data.cache import PriceCache


def make_point(ticker: str, price: float = 100.0) -> PricePoint:
    return PricePoint(
        ticker=ticker,
        price=price,
        previous_price=price - 1,
        change=1.0,
        change_percent=1.0,
        timestamp=datetime.now(UTC),
    )


async def test_get_missing_ticker_returns_none():
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
    second = make_point("AAPL", price=105.0)
    await cache.update([second])
    assert await cache.get("AAPL") == second


async def test_snapshot_reflects_all_updated_tickers():
    cache = PriceCache()
    await cache.update([make_point("AAPL"), make_point("GOOGL")])
    snapshot = await cache.snapshot()
    assert set(snapshot.keys()) == {"AAPL", "GOOGL"}


async def test_snapshot_is_a_copy_not_a_live_view():
    cache = PriceCache()
    await cache.update([make_point("AAPL")])
    snapshot = await cache.snapshot()
    snapshot["GOOGL"] = make_point("GOOGL")
    assert await cache.get("GOOGL") is None


async def test_concurrent_updates_do_not_lose_writes():
    cache = PriceCache()

    async def updater(ticker: str, count: int) -> None:
        for i in range(count):
            await cache.update([make_point(ticker, price=float(i))])

    tickers = [f"T{i}" for i in range(20)]
    await asyncio.gather(*(updater(t, 10) for t in tickers))

    snapshot = await cache.snapshot()
    assert set(snapshot.keys()) == set(tickers)
    for t in tickers:
        assert snapshot[t].price == 9.0
