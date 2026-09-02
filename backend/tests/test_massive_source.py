import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from massive.exceptions import BadResponse

from market_data.massive_source import DEFAULT_POLL_INTERVAL_SECONDS, MassiveSource


def make_snapshot(
    ticker: str,
    day_close: float | None,
    prev_close: float | None,
    todays_change: float | None = None,
    todays_change_percent: float | None = None,
    has_day: bool = True,
    has_prev_day: bool = True,
):
    day = SimpleNamespace(close=day_close) if has_day else None
    prev_day = SimpleNamespace(close=prev_close) if has_prev_day else None
    return SimpleNamespace(
        ticker=ticker,
        day=day,
        prev_day=prev_day,
        todays_change=todays_change,
        todays_change_percent=todays_change_percent,
    )


@pytest.fixture
def mock_client(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr("market_data.massive_source.RESTClient", MagicMock(return_value=client))
    return client


async def _instant_sleep(_seconds: float) -> None:
    return None


def test_init_constructs_rest_client_with_api_key(monkeypatch):
    rest_client_cls = MagicMock()
    monkeypatch.setattr("market_data.massive_source.RESTClient", rest_client_cls)
    MassiveSource(api_key="secret-key")
    rest_client_cls.assert_called_once_with(api_key="secret-key")


def test_default_poll_interval_is_fifteen_seconds(monkeypatch):
    monkeypatch.setattr("market_data.massive_source.RESTClient", MagicMock())
    source = MassiveSource(api_key="secret-key")
    assert source._poll_interval == DEFAULT_POLL_INTERVAL_SECONDS == 15.0


def test_custom_poll_interval_is_respected(monkeypatch):
    monkeypatch.setattr("market_data.massive_source.RESTClient", MagicMock())
    source = MassiveSource(api_key="secret-key", poll_interval_seconds=5.0)
    assert source._poll_interval == 5.0


def test_fetch_uses_provided_todays_change_fields(mock_client):
    mock_client.get_snapshot_all.return_value = [
        make_snapshot("AAPL", day_close=190.7, prev_close=189.47, todays_change=1.23, todays_change_percent=0.65)
    ]
    source = MassiveSource(api_key="secret-key")

    points = source._fetch({"AAPL"})

    assert len(points) == 1
    point = points[0]
    assert point.ticker == "AAPL"
    assert point.price == 190.7
    assert point.previous_price == 189.47
    assert point.change == 1.23
    assert point.change_percent == 0.65


def test_fetch_computes_change_when_todays_change_fields_missing(mock_client):
    mock_client.get_snapshot_all.return_value = [make_snapshot("AAPL", day_close=190.0, prev_close=185.0)]
    source = MassiveSource(api_key="secret-key")

    point = source._fetch({"AAPL"})[0]

    assert point.change == 5.0
    assert point.change_percent == pytest.approx(5.0 / 185.0 * 100)


def test_fetch_calls_snapshot_endpoint_with_requested_tickers(mock_client):
    mock_client.get_snapshot_all.return_value = []
    source = MassiveSource(api_key="secret-key")

    source._fetch({"AAPL", "GOOGL"})

    _, kwargs = mock_client.get_snapshot_all.call_args
    assert kwargs["market_type"] == "stocks"
    assert set(kwargs["tickers"]) == {"AAPL", "GOOGL"}


def test_fetch_skips_ticker_with_no_day_data(mock_client):
    mock_client.get_snapshot_all.return_value = [
        make_snapshot("AAPL", day_close=None, prev_close=189.47, has_day=False)
    ]
    source = MassiveSource(api_key="secret-key")
    assert source._fetch({"AAPL"}) == []


def test_fetch_skips_ticker_with_no_prev_day_data(mock_client):
    mock_client.get_snapshot_all.return_value = [
        make_snapshot("AAPL", day_close=190.7, prev_close=None, has_prev_day=False)
    ]
    source = MassiveSource(api_key="secret-key")
    assert source._fetch({"AAPL"}) == []


def test_fetch_skips_ticker_with_null_day_close(mock_client):
    mock_client.get_snapshot_all.return_value = [make_snapshot("AAPL", day_close=None, prev_close=189.47)]
    source = MassiveSource(api_key="secret-key")
    assert source._fetch({"AAPL"}) == []


def test_fetch_skips_only_the_affected_ticker(mock_client):
    mock_client.get_snapshot_all.return_value = [
        make_snapshot("AAPL", day_close=None, prev_close=189.47, has_day=False),
        make_snapshot("GOOGL", day_close=175.0, prev_close=174.0),
    ]
    source = MassiveSource(api_key="secret-key")

    points = source._fetch({"AAPL", "GOOGL"})

    assert [p.ticker for p in points] == ["GOOGL"]


async def test_stream_yields_fetched_batch(mock_client, monkeypatch):
    monkeypatch.setattr("market_data.massive_source.asyncio.sleep", _instant_sleep)
    mock_client.get_snapshot_all.return_value = [make_snapshot("AAPL", day_close=190.0, prev_close=185.0)]
    source = MassiveSource(api_key="secret-key")

    gen = source.stream(lambda: {"AAPL"})
    try:
        batch = await gen.__anext__()
    finally:
        await gen.aclose()

    assert [p.ticker for p in batch] == ["AAPL"]


async def test_stream_skips_fetch_when_no_tickers_watched(mock_client, monkeypatch):
    calls = {"n": 0}

    async def counting_sleep(_seconds: float) -> None:
        calls["n"] += 1
        if calls["n"] >= 3:
            raise asyncio.CancelledError

    monkeypatch.setattr("market_data.massive_source.asyncio.sleep", counting_sleep)
    source = MassiveSource(api_key="secret-key")

    gen = source.stream(lambda: set())
    with pytest.raises(asyncio.CancelledError):
        await gen.__anext__()

    mock_client.get_snapshot_all.assert_not_called()


async def test_stream_recovers_after_bad_response(mock_client, monkeypatch):
    monkeypatch.setattr("market_data.massive_source.asyncio.sleep", _instant_sleep)
    good_batch = [make_snapshot("AAPL", day_close=190.0, prev_close=185.0)]
    mock_client.get_snapshot_all.side_effect = [BadResponse("rate limited"), good_batch]
    source = MassiveSource(api_key="secret-key")

    gen = source.stream(lambda: {"AAPL"})
    try:
        batch = await gen.__anext__()
    finally:
        await gen.aclose()

    assert [p.ticker for p in batch] == ["AAPL"]
    assert mock_client.get_snapshot_all.call_count == 2
