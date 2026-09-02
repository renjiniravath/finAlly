from datetime import UTC, datetime

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
    assert point.timestamp is now


def test_price_point_is_frozen():
    point = PricePoint(
        ticker="AAPL",
        price=101.0,
        previous_price=100.0,
        change=1.0,
        change_percent=1.0,
        timestamp=datetime.now(UTC),
    )
    try:
        point.price = 200.0
    except AttributeError:
        pass
    else:
        raise AssertionError("PricePoint should be immutable")


def test_market_data_source_is_abstract():
    try:
        MarketDataSource()
    except TypeError:
        pass
    else:
        raise AssertionError("MarketDataSource should not be directly instantiable")
