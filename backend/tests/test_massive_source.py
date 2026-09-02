import asyncio
from types import SimpleNamespace

import pytest
from massive.exceptions import BadResponse

from market_data.massive_source import MassiveSource


def make_snapshot(
    ticker: str,
    close: float = 190.7,
    prev_close: float = 189.47,
    todays_change: float | None = 1.23,
    todays_change_percent: float | None = 0.65,
    has_day: bool = True,
    has_prev_day: bool = True,
):
    day_obj = SimpleNamespace(close=close) if has_day else None
    prev_day_obj = SimpleNamespace(close=prev_close) if has_prev_day else None
    return SimpleNamespace(
        ticker=ticker,
        day=day_obj,
        prev_day=prev_day_obj,
        todays_change=todays_change,
        todays_change_percent=todays_change_percent,
    )


@pytest.fixture
def source() -> MassiveSource:
    return MassiveSource(api_key="test-key", poll_interval_seconds=0.0)


def test_fetch_builds_price_points_from_snapshot(source, monkeypatch):
    snapshot = make_snapshot("AAPL", close=190.7, prev_close=189.47)
    monkeypatch.setattr(source._client, "get_snapshot_all", lambda **kwargs: [snapshot])

    points = source._fetch({"AAPL"})

    assert len(points) == 1
    point = points[0]
    assert point.ticker == "AAPL"
    assert point.price == 190.7
    assert point.previous_price == 189.47
    assert point.change == 1.23
    assert point.change_percent == 0.65


def test_fetch_passes_stocks_market_type_and_ticker_list(source, monkeypatch):
    captured = {}

    def fake_get_snapshot_all(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(source._client, "get_snapshot_all", fake_get_snapshot_all)
    source._fetch({"AAPL", "GOOGL"})

    assert captured["market_type"] == "stocks"
    assert set(captured["tickers"]) == {"AAPL", "GOOGL"}


def test_fetch_computes_change_when_todays_change_missing(source, monkeypatch):
    snapshot = make_snapshot(
        "AAPL", close=200.0, prev_close=100.0, todays_change=None, todays_change_percent=None
    )
    monkeypatch.setattr(source._client, "get_snapshot_all", lambda **kwargs: [snapshot])

    point = source._fetch({"AAPL"})[0]

    assert point.change == pytest.approx(100.0)
    assert point.change_percent == pytest.approx(100.0)


def test_fetch_skips_ticker_with_no_day_data_yet(source, monkeypatch):
    pre_market = make_snapshot("AAPL", has_day=False)
    normal = make_snapshot("GOOGL")
    monkeypatch.setattr(source._client, "get_snapshot_all", lambda **kwargs: [pre_market, normal])

    points = source._fetch({"AAPL", "GOOGL"})

    tickers = {p.ticker for p in points}
    assert tickers == {"GOOGL"}


def test_fetch_skips_ticker_with_no_prev_day_data(source, monkeypatch):
    snapshot = make_snapshot("AAPL", has_prev_day=False)
    monkeypatch.setattr(source._client, "get_snapshot_all", lambda **kwargs: [snapshot])

    points = source._fetch({"AAPL"})

    assert points == []


async def test_stream_skips_fetch_when_no_tickers(source, monkeypatch):
    calls = []
    monkeypatch.setattr(source, "_fetch", lambda tickers: calls.append(tickers) or [])

    aiter = source.stream(lambda: set())
    # nothing to yield, but the loop should not hang: force one iteration then bail out
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(aiter.__anext__(), timeout=0.05)
    assert calls == []


async def test_stream_recovers_after_bad_response(monkeypatch):
    source = MassiveSource(api_key="test-key", poll_interval_seconds=0.0)
    snapshot = make_snapshot("AAPL")
    call_count = {"n": 0}

    def flaky_get_snapshot_all(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise BadResponse("rate limited")
        return [snapshot]

    monkeypatch.setattr(source._client, "get_snapshot_all", flaky_get_snapshot_all)

    aiter = source.stream(lambda: {"AAPL"})
    batch = await aiter.__anext__()

    assert call_count["n"] == 2
    assert batch[0].ticker == "AAPL"
