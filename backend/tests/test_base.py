from datetime import UTC, datetime

import pytest

from market_data.base import MarketDataSource, PricePoint


def test_price_point_is_frozen():
    point = PricePoint(
        ticker="AAPL",
        price=190.5,
        previous_price=189.0,
        change=1.5,
        change_percent=0.79,
        timestamp=datetime.now(UTC),
    )
    assert point.ticker == "AAPL"
    with pytest.raises(AttributeError):
        point.price = 200.0  # type: ignore[misc]


def test_market_data_source_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        MarketDataSource()  # type: ignore[abstract]


def test_market_data_source_subclass_must_implement_stream():
    class Incomplete(MarketDataSource):
        pass

    with pytest.raises(TypeError):
        Incomplete()  # type: ignore[abstract]
