import math

import pytest

from market_data import simulator as simulator_module
from market_data.simulator import DEFAULT_TICKERS, SimulatorSource, TickerConfig


def test_tick_returns_a_point_per_requested_ticker():
    sim = SimulatorSource(seed=42)
    points = sim._tick({"AAPL", "GOOGL", "TSLA"})
    assert {p.ticker for p in points} == {"AAPL", "GOOGL", "TSLA"}


def test_prices_start_from_seed_price():
    sim = SimulatorSource(seed=42)
    points = {p.ticker: p for p in sim._tick({"AAPL"})}
    aapl_seed = next(c.seed_price for c in DEFAULT_TICKERS if c.ticker == "AAPL")
    assert points["AAPL"].previous_price == aapl_seed


def test_prices_move_between_ticks():
    sim = SimulatorSource(seed=42)
    first = sim._tick({"AAPL"})
    second = sim._tick({"AAPL"})
    assert first[0].price != second[0].price


def test_same_seed_produces_identical_price_path():
    sim_a = SimulatorSource(seed=42)
    sim_b = SimulatorSource(seed=42)
    for _ in range(10):
        points_a = sim_a._tick({"AAPL", "GOOGL"})
        points_b = sim_b._tick({"AAPL", "GOOGL"})
        assert [p.price for p in points_a] == [p.price for p in points_b]


def test_prices_stay_positive_over_many_ticks():
    sim = SimulatorSource(seed=123)
    for _ in range(2000):
        points = sim._tick({"TSLA"})  # highest volatility ticker
        assert points[0].price > 0


def test_change_and_change_percent_are_consistent():
    sim = SimulatorSource(seed=5)
    point = sim._tick({"AAPL"})[0]
    assert point.change == pytest.approx(point.price - point.previous_price)
    assert point.change_percent == pytest.approx(
        (point.price - point.previous_price) / point.previous_price * 100
    )


def test_unknown_ticker_gets_synthesized_config_with_own_sector():
    sim = SimulatorSource(seed=1)
    points = sim._tick({"ZZZZ"})
    assert points[0].previous_price == 100.0
    assert sim._configs["ZZZZ"].sector == "ZZZZ"


def test_same_sector_tickers_are_correlated():
    sim = SimulatorSource(seed=7)
    ups_together = 0
    trials = 500
    for _ in range(trials):
        points = {p.ticker: p for p in sim._tick({"AAPL", "GOOGL", "TSLA"})}  # AAPL/GOOGL tech, TSLA auto
        if (points["AAPL"].change > 0) == (points["GOOGL"].change > 0):
            ups_together += 1
    assert ups_together > 300  # well above the ~250 chance baseline for independent coin flips


def test_different_sector_tickers_are_less_correlated_than_same_sector():
    sim = SimulatorSource(seed=11)
    same_sector_agree = 0
    diff_sector_agree = 0
    trials = 500
    for _ in range(trials):
        points = {p.ticker: p for p in sim._tick({"AAPL", "GOOGL", "TSLA"})}
        if (points["AAPL"].change > 0) == (points["GOOGL"].change > 0):
            same_sector_agree += 1
        if (points["AAPL"].change > 0) == (points["TSLA"].change > 0):
            diff_sector_agree += 1
    assert same_sector_agree > diff_sector_agree


def test_event_jump_forces_large_magnitude_move(monkeypatch):
    monkeypatch.setattr(simulator_module, "EVENT_PROBABILITY_PER_TICK", 1.0)
    sim = SimulatorSource(seed=99)
    point = sim._tick({"AAPL"})[0]
    assert abs(point.change_percent) >= 1.5  # GBM tick alone moves ~0.2%; forced event adds 2-5%


def test_no_event_probability_zero_keeps_moves_small(monkeypatch):
    monkeypatch.setattr(simulator_module, "EVENT_PROBABILITY_PER_TICK", 0.0)
    sim = SimulatorSource(seed=99)
    for _ in range(200):
        point = sim._tick({"AAPL"})[0]
        assert abs(point.change_percent) < 2.0


async def test_stream_yields_batches_indefinitely():
    sim = SimulatorSource(seed=1)
    stream = sim.stream(lambda: {"AAPL"})
    first = await anext(stream)
    second = await anext(stream)
    assert first[0].ticker == "AAPL"
    assert second[0].ticker == "AAPL"


async def test_stream_reflects_ticker_set_changes_between_cycles():
    sim = SimulatorSource(seed=1)
    calls = iter([{"AAPL"}, {"AAPL", "GOOGL"}])
    stream = sim.stream(lambda: next(calls))
    first = await anext(stream)
    second = await anext(stream)
    assert {p.ticker for p in first} == {"AAPL"}
    assert {p.ticker for p in second} == {"AAPL", "GOOGL"}


def test_weights_sum_to_unit_variance():
    total_variance = (
        simulator_module.W_MARKET**2 + simulator_module.W_SECTOR**2 + simulator_module.W_IDIO**2
    )
    assert total_variance == pytest.approx(1.0)


def test_default_tickers_match_plan_seed_list():
    tickers = {c.ticker for c in DEFAULT_TICKERS}
    assert tickers == {"AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX"}
