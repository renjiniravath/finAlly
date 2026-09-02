from datetime import UTC, datetime

from market_data.base import MarketDataSource, PricePoint


def test_price_point_is_frozen():
    point = PricePoint(
        ticker="AAPL",
        price=190.0,
        previous_price=189.0,
        change=1.0,
        change_percent=0.529,
        timestamp=datetime.now(UTC),
    )
    assert point.ticker == "AAPL"
    try:
        point.price = 200.0
        assert False, "PricePoint should be immutable"
    except AttributeError:
        pass


def test_market_data_source_is_abstract():
    try:
        MarketDataSource()
        assert False, "MarketDataSource should not be instantiable directly"
    except TypeError:
        pass


def test_market_data_source_requires_stream_implementation():
    class Incomplete(MarketDataSource):
        pass

    try:
        Incomplete()
        assert False, "subclass without stream() should not be instantiable"
    except TypeError:
        pass
