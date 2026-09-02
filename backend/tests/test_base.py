from datetime import UTC, datetime

import pytest

from market_data.base import MarketDataSource, PricePoint


def test_price_point_fields():
    point = PricePoint(
        ticker="AAPL",
        price=191.5,
        previous_price=190.0,
        change=1.5,
        change_percent=1.5 / 190.0 * 100,
        timestamp=datetime.now(UTC),
    )
    assert point.ticker == "AAPL"
    assert point.price == 191.5
    assert point.change == pytest.approx(point.price - point.previous_price)
    assert point.change_percent == pytest.approx(point.change / point.previous_price * 100)


def test_price_point_is_frozen():
    point = PricePoint(
        ticker="AAPL",
        price=191.5,
        previous_price=190.0,
        change=1.5,
        change_percent=0.79,
        timestamp=datetime.now(UTC),
    )
    with pytest.raises(AttributeError):
        point.price = 200.0


def test_market_data_source_is_abstract():
    with pytest.raises(TypeError):
        MarketDataSource()


def test_market_data_source_requires_stream_implementation():
    class Incomplete(MarketDataSource):
        pass

    with pytest.raises(TypeError):
        Incomplete()
