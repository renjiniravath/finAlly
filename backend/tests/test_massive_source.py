import asyncio
from types import SimpleNamespace

import pytest
from massive.exceptions import BadResponse

from market_data import massive_source as massive_source_module
from market_data.massive_source import MassiveSource


def make_snapshot(
    ticker: str,
    day_close: float | None,
    prev_close: float | None,
    todays_change: float | None = None,
    todays_change_percent: float | None = None,
) -> SimpleNamespace:
    day = SimpleNamespace(close=day_close) if day_close is not None else None
    prev_day = SimpleNamespace(close=prev_close) if prev_close is not None else None
    return SimpleNamespace(
        ticker=ticker,
        day=day,
        prev_day=prev_day,
        todays_change=todays_change,
        todays_change_percent=todays_change_percent,
    )


class FakeClient:
    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key
        self.calls: list[list[str]] = []
        self._responses: list = []

    def queue_response(self, response) -> None:
        self._responses.append(response)

    def get_snapshot_all(self, market_type: str = "stocks", tickers: list[str] | None = None):
        self.calls.append(list(tickers or []))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_source(monkeypatch, poll_interval_seconds: float = 0.0) -> tuple[MassiveSource, FakeClient]:
    fake_client = FakeClient()
    monkeypatch.setattr(massive_source_module, "RESTClient", lambda api_key=None: fake_client)
    source = MassiveSource(api_key="test-key", poll_interval_seconds=poll_interval_seconds)
    return source, fake_client


def test_fetch_uses_todays_change_fields_when_present():
    source = MassiveSource.__new__(MassiveSource)
    snapshot = make_snapshot("AAPL", day_close=101.0, prev_close=100.0, todays_change=1.0, todays_change_percent=1.0)
    source._client = SimpleNamespace(get_snapshot_all=lambda **kw: [snapshot])

    points = source._fetch({"AAPL"})

    assert len(points) == 1
    point = points[0]
    assert point.ticker == "AAPL"
    assert point.price == 101.0
    assert point.previous_price == 100.0
    assert point.change == 1.0
    assert point.change_percent == 1.0


def test_fetch_computes_change_when_fields_missing():
    source = MassiveSource.__new__(MassiveSource)
    snapshot = make_snapshot("AAPL", day_close=102.0, prev_close=100.0)
    source._client = SimpleNamespace(get_snapshot_all=lambda **kw: [snapshot])

    points = source._fetch({"AAPL"})

    point = points[0]
    assert point.change == 2.0
    assert point.change_percent == 2.0


def test_fetch_skips_ticker_with_no_day_data():
    source = MassiveSource.__new__(MassiveSource)
    snapshot = make_snapshot("AAPL", day_close=None, prev_close=100.0)
    source._client = SimpleNamespace(get_snapshot_all=lambda **kw: [snapshot])

    points = source._fetch({"AAPL"})

    assert points == []


def test_fetch_skips_ticker_with_no_prev_day_data():
    source = MassiveSource.__new__(MassiveSource)
    snapshot = make_snapshot("AAPL", day_close=101.0, prev_close=None)
    source._client = SimpleNamespace(get_snapshot_all=lambda **kw: [snapshot])

    points = source._fetch({"AAPL"})

    assert points == []


def test_fetch_passes_through_multiple_tickers():
    source = MassiveSource.__new__(MassiveSource)
    snapshots = [
        make_snapshot("AAPL", 101.0, 100.0),
        make_snapshot("GOOGL", 176.0, 175.0),
    ]
    source._client = SimpleNamespace(get_snapshot_all=lambda **kw: snapshots)

    points = source._fetch({"AAPL", "GOOGL"})

    assert {p.ticker for p in points} == {"AAPL", "GOOGL"}


async def test_stream_yields_fetched_batch(monkeypatch):
    source, fake_client = make_source(monkeypatch)
    fake_client.queue_response([make_snapshot("AAPL", 101.0, 100.0)])

    gen = source.stream(lambda: {"AAPL"})
    batch = await asyncio.wait_for(anext(gen), timeout=1)

    assert [p.ticker for p in batch] == ["AAPL"]
    assert fake_client.calls == [["AAPL"]]
    await gen.aclose()


async def test_stream_recovers_from_bad_response(monkeypatch):
    source, fake_client = make_source(monkeypatch)
    fake_client.queue_response(BadResponse("rate limited"))
    fake_client.queue_response([make_snapshot("AAPL", 101.0, 100.0)])

    gen = source.stream(lambda: {"AAPL"})
    batch = await asyncio.wait_for(anext(gen), timeout=1)

    assert [p.ticker for p in batch] == ["AAPL"]
    assert len(fake_client.calls) == 2
    await gen.aclose()


async def test_stream_skips_fetch_when_no_tickers(monkeypatch):
    source, fake_client = make_source(monkeypatch)

    gen = source.stream(lambda: set())
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(anext(gen), timeout=0.2)

    assert fake_client.calls == []
    await gen.aclose()
