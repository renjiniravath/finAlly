from unittest.mock import MagicMock, patch

import pytest
from massive.exceptions import BadResponse
from massive.rest.models.aggs import Agg
from massive.rest.models.snapshot import TickerSnapshot

from market_data.massive_source import MassiveSource


def make_snapshot(
    ticker: str,
    close: float,
    prev_close: float,
    todays_change: float | None = None,
    todays_change_percent: float | None = None,
    day: Agg | None = "unset",
    prev_day: Agg | None = "unset",
) -> TickerSnapshot:
    if day == "unset":
        day = Agg(close=close)
    if prev_day == "unset":
        prev_day = Agg(close=prev_close)
    return TickerSnapshot(
        ticker=ticker,
        day=day,
        prev_day=prev_day,
        todays_change=todays_change,
        todays_change_percent=todays_change_percent,
    )


@patch("market_data.massive_source.RESTClient")
def test_init_constructs_client_with_api_key(mock_rest_client):
    MassiveSource(api_key="test-key")
    mock_rest_client.assert_called_once_with(api_key="test-key")


@patch("market_data.massive_source.RESTClient")
def test_fetch_parses_snapshot_into_price_points(mock_rest_client):
    mock_client = mock_rest_client.return_value
    mock_client.get_snapshot_all.return_value = [
        make_snapshot("AAPL", close=190.7, prev_close=189.47, todays_change=1.23, todays_change_percent=0.65),
    ]
    source = MassiveSource(api_key="test-key")

    points = source._fetch({"AAPL"})

    assert len(points) == 1
    point = points[0]
    assert point.ticker == "AAPL"
    assert point.price == 190.7
    assert point.previous_price == 189.47
    assert point.change == 1.23
    assert point.change_percent == 0.65


@patch("market_data.massive_source.RESTClient")
def test_fetch_calls_get_snapshot_all_with_stocks_and_tickers(mock_rest_client):
    mock_client = mock_rest_client.return_value
    mock_client.get_snapshot_all.return_value = []
    source = MassiveSource(api_key="test-key")

    source._fetch({"AAPL", "GOOGL"})

    _, kwargs = mock_client.get_snapshot_all.call_args
    assert kwargs["market_type"] == "stocks"
    assert set(kwargs["tickers"]) == {"AAPL", "GOOGL"}


@patch("market_data.massive_source.RESTClient")
def test_fetch_falls_back_to_computed_change_when_todays_change_missing(mock_rest_client):
    mock_client = mock_rest_client.return_value
    mock_client.get_snapshot_all.return_value = [
        make_snapshot("AAPL", close=190.0, prev_close=100.0, todays_change=None, todays_change_percent=None),
    ]
    source = MassiveSource(api_key="test-key")

    point = source._fetch({"AAPL"})[0]

    assert point.change == pytest.approx(90.0)
    assert point.change_percent == pytest.approx(90.0)


@patch("market_data.massive_source.RESTClient")
def test_fetch_skips_ticker_with_no_day_data(mock_rest_client):
    mock_client = mock_rest_client.return_value
    mock_client.get_snapshot_all.return_value = [
        make_snapshot("AAPL", close=190.0, prev_close=189.0),
        make_snapshot("NEWCO", close=0, prev_close=0, day=None, prev_day=None),
    ]
    source = MassiveSource(api_key="test-key")

    points = source._fetch({"AAPL", "NEWCO"})

    assert {p.ticker for p in points} == {"AAPL"}


@patch("market_data.massive_source.RESTClient")
def test_fetch_skips_ticker_with_no_prev_day_data(mock_rest_client):
    mock_client = mock_rest_client.return_value
    mock_client.get_snapshot_all.return_value = [
        make_snapshot("AAPL", close=190.0, prev_close=189.0, prev_day=None),
    ]
    source = MassiveSource(api_key="test-key")

    points = source._fetch({"AAPL"})

    assert points == []


@patch("market_data.massive_source.RESTClient")
def test_fetch_skips_ticker_with_null_close(mock_rest_client):
    mock_client = mock_rest_client.return_value
    mock_client.get_snapshot_all.return_value = [
        make_snapshot("AAPL", close=190.0, prev_close=189.0, day=Agg(close=None)),
    ]
    source = MassiveSource(api_key="test-key")

    points = source._fetch({"AAPL"})

    assert points == []


@patch("market_data.massive_source.RESTClient")
async def test_stream_yields_fetched_batch(mock_rest_client):
    mock_client = mock_rest_client.return_value
    mock_client.get_snapshot_all.return_value = [
        make_snapshot("AAPL", close=190.0, prev_close=189.0),
    ]
    source = MassiveSource(api_key="test-key", poll_interval_seconds=0)

    stream = source.stream(lambda: {"AAPL"})
    batch = await anext(stream)

    assert batch[0].ticker == "AAPL"


@patch("market_data.massive_source.RESTClient")
async def test_stream_skips_fetch_when_no_tickers(mock_rest_client):
    mock_client = mock_rest_client.return_value
    source = MassiveSource(api_key="test-key", poll_interval_seconds=0)

    calls = iter([set(), {"AAPL"}])
    mock_client.get_snapshot_all.return_value = [make_snapshot("AAPL", close=190.0, prev_close=189.0)]

    stream = source.stream(lambda: next(calls))
    # First cycle has no tickers, so get_snapshot_all should not be called yet;
    # the generator should move on to the second cycle and yield there instead.
    batch = await anext(stream)

    assert batch[0].ticker == "AAPL"
    mock_client.get_snapshot_all.assert_called_once()


@patch("market_data.massive_source.RESTClient")
async def test_stream_recovers_from_bad_response_and_keeps_running(mock_rest_client):
    mock_client = mock_rest_client.return_value
    mock_client.get_snapshot_all.side_effect = [
        BadResponse("rate limited"),
        [make_snapshot("AAPL", close=190.0, prev_close=189.0)],
    ]
    source = MassiveSource(api_key="test-key", poll_interval_seconds=0)

    stream = source.stream(lambda: {"AAPL"})
    batch = await anext(stream)

    assert batch[0].ticker == "AAPL"
    assert mock_client.get_snapshot_all.call_count == 2
