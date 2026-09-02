import asyncio
from datetime import UTC, datetime

from market_data.base import PricePoint
from market_data.cache import PriceCache


def make_point(ticker: str, price: float, previous: float | None = None) -> PricePoint:
    previous = previous if previous is not None else price
    return PricePoint(
        ticker=ticker,
        price=price,
        previous_price=previous,
        change=price - previous,
        change_percent=(price - previous) / previous * 100 if previous else 0.0,
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


async def test_update_overwrites_previous_value_for_same_ticker():
    cache = PriceCache()
    await cache.update([make_point("AAPL", 190.0)])
    second = make_point("AAPL", 195.0, previous=190.0)
    await cache.update([second])
    assert await cache.get("AAPL") == second


async def test_snapshot_contains_all_tickers():
    cache = PriceCache()
    await cache.update([make_point("AAPL", 190.0), make_point("GOOGL", 175.0)])
    snapshot = await cache.snapshot()
    assert set(snapshot) == {"AAPL", "GOOGL"}
    assert snapshot["AAPL"].price == 190.0
    assert snapshot["GOOGL"].price == 175.0


async def test_snapshot_is_a_copy_not_a_live_view():
    cache = PriceCache()
    await cache.update([make_point("AAPL", 190.0)])
    snapshot = await cache.snapshot()
    snapshot["AAPL"] = make_point("AAPL", 999.0)
    snapshot["MSFT"] = make_point("MSFT", 420.0)

    fresh = await cache.snapshot()
    assert fresh["AAPL"].price == 190.0
    assert "MSFT" not in fresh


async def test_update_with_empty_batch_is_a_no_op():
    cache = PriceCache()
    await cache.update([make_point("AAPL", 190.0)])
    await cache.update([])
    assert (await cache.get("AAPL")).price == 190.0


async def test_concurrent_updates_do_not_corrupt_state():
    cache = PriceCache()
    tickers = [f"T{i}" for i in range(50)]

    async def write(ticker: str) -> None:
        for price in (100.0, 101.0, 102.0):
            await cache.update([make_point(ticker, price)])

    await asyncio.gather(*(write(t) for t in tickers))

    snapshot = await cache.snapshot()
    assert set(snapshot) == set(tickers)
    assert all(point.price == 102.0 for point in snapshot.values())
