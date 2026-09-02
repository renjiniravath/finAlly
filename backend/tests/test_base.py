from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from market_data.base import MarketDataSource, PricePoint


def test_price_point_fields():
    now = datetime.now(UTC)
    point = PricePoint(
        ticker="AAPL",
        price=101.0,
        previous_price=100.0,
        change=1.0,
        change_percent=1.0,
        timestamp=now,
    )
    assert point.ticker == "AAPL"
    assert point.price == 101.0
    assert point.previous_price == 100.0
    assert point.change == 1.0
    assert point.change_percent == 1.0
    assert point.timestamp == now


def test_price_point_is_frozen():
    point = PricePoint(
        ticker="AAPL",
        price=101.0,
        previous_price=100.0,
        change=1.0,
        change_percent=1.0,
        timestamp=datetime.now(UTC),
    )
    with pytest.raises(FrozenInstanceError):
        point.price = 999.0


def test_market_data_source_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        MarketDataSource()


def test_subclass_missing_stream_cannot_be_instantiated():
    class Incomplete(MarketDataSource):
        pass

    with pytest.raises(TypeError):
        Incomplete()


def test_subclass_implementing_stream_can_be_instantiated():
    class Minimal(MarketDataSource):
        async def stream(self, get_tickers):
            if False:
                yield []

    Minimal()
