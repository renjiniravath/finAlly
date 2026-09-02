import asyncio
from types import SimpleNamespace

import pytest

from market_data.massive_source import MassiveSource


def make_snapshot(ticker, close, prev_close, todays_change=None, todays_change_percent=None, day=True, prev_day=True):
    return SimpleNamespace(
        ticker=ticker,
        day=SimpleNamespace(close=close) if day else None,
        prev_day=SimpleNamespace(close=prev_close) if prev_day else None,
        todays_change=todays_change,
        todays_change_percent=todays_change_percent,
    )


def test_fetch_parses_normal_snapshot(monkeypatch):
    source = MassiveSource(api_key="test-key")
    monkeypatch.setattr(
        source._client,
        "get_snapshot_all",
        lambda market_type, tickers: [make_snapshot("AAPL", 190.7, 189.47, 1.23, 0.65)],
    )
    points = source._fetch({"AAPL"})
    assert len(points) == 1
    point = points[0]
    assert point.ticker == "AAPL"
    assert point.price == 190.7
    assert point.previous_price == 189.47
    assert point.change == 1.23
    assert point.change_percent == 0.65


def test_fetch_computes_change_when_not_provided(monkeypatch):
    source = MassiveSource(api_key="test-key")
    monkeypatch.setattr(
        source._client,
        "get_snapshot_all",
        lambda market_type, tickers: [make_snapshot("AAPL", 200.0, 100.0)],
    )
    points = source._fetch({"AAPL"})
    point = points[0]
    assert point.change == 100.0
    assert point.change_percent == 100.0


def test_fetch_skips_ticker_with_no_day_data_yet(monkeypatch):
    source = MassiveSource(api_key="test-key")
    monkeypatch.setattr(
        source._client,
        "get_snapshot_all",
        lambda market_type, tickers: [
            make_snapshot("AAPL", 190.7, 189.47),
            make_snapshot("NEWCO", None, None, day=False, prev_day=False),
        ],
    )
    points = source._fetch({"AAPL", "NEWCO"})
    tickers = {p.ticker for p in points}
    assert tickers == {"AAPL"}


def test_fetch_skips_ticker_with_null_day_close(monkeypatch):
    source = MassiveSource(api_key="test-key")
    monkeypatch.setattr(
        source._client,
        "get_snapshot_all",
        lambda market_type, tickers: [make_snapshot("AAPL", None, 189.47)],
    )
    points = source._fetch({"AAPL"})
    assert points == []


def test_fetch_requests_all_given_tickers(monkeypatch):
    source = MassiveSource(api_key="test-key")
    seen = {}

    def fake_get_snapshot_all(market_type, tickers):
        seen["market_type"] = market_type
        seen["tickers"] = set(tickers)
        return []

    monkeypatch.setattr(source._client, "get_snapshot_all", fake_get_snapshot_all)
    source._fetch({"AAPL", "GOOGL"})
    assert seen["market_type"] == "stocks"
    assert seen["tickers"] == {"AAPL", "GOOGL"}


@pytest.mark.asyncio
async def test_stream_swallows_bad_response_and_retries_next_cycle(monkeypatch):
    from market_data.massive_source import BadResponse

    source = MassiveSource(api_key="test-key", poll_interval_seconds=0)
    calls = {"n": 0}

    def flaky_fetch(tickers):
        calls["n"] += 1
        if calls["n"] == 1:
            raise BadResponse("rate limited")
        return [make_snapshot("AAPL", 190.7, 189.47)]

    monkeypatch.setattr(source, "_fetch", flaky_fetch)

    gen = source.stream(lambda: {"AAPL"})
    # cycle 1 raises BadResponse internally (no yield); cycle 2 succeeds and yields
    batch = await gen.__anext__()
    assert calls["n"] == 2
    assert batch[0].ticker == "AAPL"


@pytest.mark.asyncio
async def test_stream_never_fetches_when_no_tickers_watched(monkeypatch):
    source = MassiveSource(api_key="test-key", poll_interval_seconds=0)
    calls = {"n": 0}
    monkeypatch.setattr(source, "_fetch", lambda tickers: calls.update(n=calls["n"] + 1) or [])

    gen = source.stream(lambda: set())
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(gen.__anext__(), timeout=0.05)
    assert calls["n"] == 0
