from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from market_data.massive_source import MassiveSource


def make_snapshot(
    ticker: str,
    day_close: float | None = 190.7,
    prev_close: float | None = 189.47,
    todays_change: float | None = 1.23,
    todays_change_percent: float | None = 0.65,
) -> SimpleNamespace:
    day = None if day_close is None else SimpleNamespace(close=day_close)
    prev_day = None if prev_close is None else SimpleNamespace(close=prev_close)
    return SimpleNamespace(
        ticker=ticker,
        day=day,
        prev_day=prev_day,
        todays_change=todays_change,
        todays_change_percent=todays_change_percent,
    )


@pytest.fixture
def source(monkeypatch) -> MassiveSource:
    monkeypatch.setattr("market_data.massive_source.RESTClient", MagicMock())
    return MassiveSource(api_key="test-key", poll_interval_seconds=0.0)


def test_fetch_parses_normal_snapshot_into_price_points(source):
    source._client.get_snapshot_all.return_value = [make_snapshot("AAPL")]

    points = source._fetch({"AAPL"})

    assert len(points) == 1
    point = points[0]
    assert point.ticker == "AAPL"
    assert point.price == 190.7
    assert point.previous_price == 189.47
    assert point.change == 1.23
    assert point.change_percent == 0.65


def test_fetch_falls_back_to_computed_change_when_missing(source):
    source._client.get_snapshot_all.return_value = [
        make_snapshot("AAPL", day_close=190.0, prev_close=189.0, todays_change=None, todays_change_percent=None)
    ]

    point = source._fetch({"AAPL"})[0]

    assert point.change == pytest.approx(1.0)
    assert point.change_percent == pytest.approx(1.0 / 189.0 * 100)


def test_fetch_skips_pre_market_ticker_with_no_day_data(source):
    source._client.get_snapshot_all.return_value = [
        make_snapshot("AAPL", day_close=None),
        make_snapshot("GOOGL"),
    ]

    points = source._fetch({"AAPL", "GOOGL"})

    assert [p.ticker for p in points] == ["GOOGL"]


def test_fetch_skips_ticker_missing_prev_day(source):
    source._client.get_snapshot_all.return_value = [make_snapshot("AAPL", prev_close=None)]

    points = source._fetch({"AAPL"})

    assert points == []


def test_fetch_calls_client_with_stocks_market_type_and_ticker_list(source):
    source._client.get_snapshot_all.return_value = []

    source._fetch({"AAPL", "GOOGL"})

    args, kwargs = source._client.get_snapshot_all.call_args
    assert kwargs["market_type"] == "stocks"
    assert set(kwargs["tickers"]) == {"AAPL", "GOOGL"}


async def test_stream_yields_fetched_batch(source):
    source._client.get_snapshot_all.return_value = [make_snapshot("AAPL")]

    agen = source.stream(lambda: {"AAPL"})
    batch = await agen.__anext__()
    await agen.aclose()

    assert batch[0].ticker == "AAPL"


async def test_stream_swallows_bad_response_and_continues(source):
    from massive.exceptions import BadResponse

    source._client.get_snapshot_all.side_effect = [
        BadResponse("rate limited"),
        [make_snapshot("AAPL")],
    ]

    agen = source.stream(lambda: {"AAPL"})
    # First cycle: BadResponse is caught internally, no batch yielded for it, loop continues to
    # the next cycle and succeeds.
    batch = await agen.__anext__()
    await agen.aclose()

    assert batch[0].ticker == "AAPL"
    assert source._client.get_snapshot_all.call_count == 2


async def test_stream_skips_fetch_when_no_tickers(source):
    calls = []

    def get_tickers() -> set[str]:
        calls.append(1)
        return set() if len(calls) == 1 else {"AAPL"}

    source._client.get_snapshot_all.return_value = [make_snapshot("AAPL")]

    agen = source.stream(get_tickers)
    batch = await agen.__anext__()
    await agen.aclose()

    assert batch[0].ticker == "AAPL"
    assert len(calls) == 2
    source._client.get_snapshot_all.assert_called_once()
