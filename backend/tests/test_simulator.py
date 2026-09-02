import asyncio

import pytest

from market_data import simulator as simulator_module
from market_data.simulator import DEFAULT_TICKERS, SimulatorSource


def test_tick_moves_price_away_from_previous():
    sim = SimulatorSource(seed=42)
    first = sim._tick({"AAPL"})
    second = sim._tick({"AAPL"})
    assert first[0].price != second[0].price
    assert second[0].previous_price == first[0].price


def test_seeded_runs_are_deterministic():
    sim_a = SimulatorSource(seed=42)
    sim_b = SimulatorSource(seed=42)

    prices_a = [sim_a._tick({"AAPL"})[0].price for _ in range(20)]
    prices_b = [sim_b._tick({"AAPL"})[0].price for _ in range(20)]

    assert prices_a == prices_b


def test_prices_stay_positive_over_many_ticks():
    sim = SimulatorSource(seed=123)
    tickers = {c.ticker for c in DEFAULT_TICKERS}
    for _ in range(2000):
        points = sim._tick(tickers)
        for p in points:
            assert p.price > 0


def test_change_fields_are_consistent():
    sim = SimulatorSource(seed=5)
    sim._tick({"AAPL"})
    points = sim._tick({"AAPL"})
    point = points[0]
    assert point.change == pytest.approx(point.price - point.previous_price)
    assert point.change_percent == pytest.approx(point.change / point.previous_price * 100)


def test_same_sector_tickers_are_correlated():
    sim = SimulatorSource(seed=7)
    ups_together = 0
    for _ in range(500):
        points = {p.ticker: p for p in sim._tick({"AAPL", "GOOGL", "TSLA"})}  # AAPL/GOOGL tech, TSLA auto
        if (points["AAPL"].change > 0) == (points["GOOGL"].change > 0):
            ups_together += 1
    assert ups_together > 300  # well above the ~250 chance baseline


def test_different_sector_tickers_are_less_correlated_than_same_sector():
    sim_same = SimulatorSource(seed=11)
    same_sector_agree = 0
    for _ in range(500):
        points = {p.ticker: p for p in sim_same._tick({"AAPL", "GOOGL"})}  # both tech
        if (points["AAPL"].change > 0) == (points["GOOGL"].change > 0):
            same_sector_agree += 1

    sim_diff = SimulatorSource(seed=11)
    diff_sector_agree = 0
    for _ in range(500):
        points = {p.ticker: p for p in sim_diff._tick({"AAPL", "JPM"})}  # tech vs financial
        if (points["AAPL"].change > 0) == (points["JPM"].change > 0):
            diff_sector_agree += 1

    assert same_sector_agree > diff_sector_agree


def test_event_jump_applies_expected_magnitude(monkeypatch):
    monkeypatch.setattr(simulator_module, "EVENT_PROBABILITY_PER_TICK", 1.0)
    sim = SimulatorSource(seed=1)
    point = sim._tick({"AAPL"})[0]
    relative_move = abs(point.change) / point.previous_price
    # GBM diffusion alone is ~0.2% per tick; a forced event adds a 2-5% jump on top,
    # so the observed move should sit well above pure-diffusion noise.
    assert relative_move > 0.015


def test_no_event_jump_keeps_move_small(monkeypatch):
    monkeypatch.setattr(simulator_module, "EVENT_PROBABILITY_PER_TICK", 0.0)
    sim = SimulatorSource(seed=2)
    point = sim._tick({"AAPL"})[0]
    relative_move = abs(point.change) / point.previous_price
    assert relative_move < 0.02


def test_new_ticker_gets_synthesized_standalone_config():
    sim = SimulatorSource(seed=3)
    points = sim._tick({"PYPL"})
    assert points[0].ticker == "PYPL"
    assert sim._configs["PYPL"].seed_price == 100.0
    assert sim._configs["PYPL"].sector == "PYPL"


def test_synthesized_ticker_does_not_correlate_with_default_sector():
    sim = SimulatorSource(seed=9)
    agree = 0
    for _ in range(500):
        points = {p.ticker: p for p in sim._tick({"AAPL", "ZZZZ"})}
        if (points["AAPL"].change > 0) == (points["ZZZZ"].change > 0):
            agree += 1
    assert agree < 320  # no persistent correlation, just chance-level agreement


def test_default_tickers_cover_plan_seed_list():
    tickers = {c.ticker for c in DEFAULT_TICKERS}
    assert tickers == {"AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX"}


async def test_stream_yields_batches_for_current_tickers(monkeypatch):
    monkeypatch.setattr(simulator_module, "TICK_SECONDS", 0)
    sim = SimulatorSource(seed=42)
    gen = sim.stream(lambda: {"AAPL", "GOOGL"})

    batch = await asyncio.wait_for(anext(gen), timeout=1)
    assert {p.ticker for p in batch} == {"AAPL", "GOOGL"}
    await gen.aclose()


async def test_stream_reflects_ticker_set_changes_between_cycles(monkeypatch):
    monkeypatch.setattr(simulator_module, "TICK_SECONDS", 0)
    sim = SimulatorSource(seed=42)
    tickers = {"AAPL"}
    gen = sim.stream(lambda: tickers)

    first = await asyncio.wait_for(anext(gen), timeout=1)
    assert {p.ticker for p in first} == {"AAPL"}

    tickers = {"AAPL", "MSFT"}
    second = await asyncio.wait_for(anext(gen), timeout=1)
    assert {p.ticker for p in second} == {"AAPL", "MSFT"}
    await gen.aclose()
