from collections.abc import AsyncIterator, Callable
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from market_data.base import MarketDataSource, PricePoint


def test_price_point_holds_all_fields():
    now = datetime.now(UTC)
    point = PricePoint(
        ticker="AAPL",
        price=190.5,
        previous_price=190.0,
        change=0.5,
        change_percent=0.263,
        timestamp=now,
    )
    assert point.ticker == "AAPL"
    assert point.price == 190.5
    assert point.previous_price == 190.0
    assert point.change == 0.5
    assert point.change_percent == 0.263
    assert point.timestamp is now


def test_price_point_is_frozen():
    point = PricePoint(
        ticker="AAPL",
        price=190.5,
        previous_price=190.0,
        change=0.5,
        change_percent=0.263,
        timestamp=datetime.now(UTC),
    )
    with pytest.raises(FrozenInstanceError):
        point.price = 200.0


def test_market_data_source_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        MarketDataSource()


def test_market_data_source_subclass_must_implement_stream():
    class Incomplete(MarketDataSource):
        pass

    with pytest.raises(TypeError):
        Incomplete()


async def test_market_data_source_subclass_with_stream_is_instantiable():
    class Minimal(MarketDataSource):
        async def stream(self, get_tickers: Callable[[], set[str]]) -> AsyncIterator[list[PricePoint]]:
            yield []

    source = Minimal()
    gen = source.stream(lambda: set())
    batch = await gen.__anext__()
    assert batch == []
