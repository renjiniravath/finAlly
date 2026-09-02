import math

import pytest

from market_data.simulator import (
    DEFAULT_TICKERS,
    DT_YEARS,
    EVENT_MAGNITUDE_RANGE,
    SimulatorSource,
    TickerConfig,
    W_IDIO,
    W_MARKET,
    W_SECTOR,
)


def test_factor_weights_variances_sum_to_one():
    assert math.isclose(W_MARKET**2 + W_SECTOR**2 + W_IDIO**2, 1.0, rel_tol=1e-9)


def test_default_tickers_match_plan_seed_list():
    tickers = {c.ticker for c in DEFAULT_TICKERS}
    assert tickers == {"AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX"}


def test_tick_returns_one_price_point_per_requested_ticker():
    sim = SimulatorSource(seed=1)
    points = sim._tick({"AAPL", "GOOGL", "TSLA"})
    assert {p.ticker for p in points} == {"AAPL", "GOOGL", "TSLA"}


def test_tick_prices_start_from_seed_price():
    sim = SimulatorSource(seed=1)
    points = {p.ticker: p for p in sim._tick({"AAPL"})}
    aapl_config = next(c for c in DEFAULT_TICKERS if c.ticker == "AAPL")
    assert points["AAPL"].previous_price == aapl_config.seed_price


def test_change_and_change_percent_are_internally_consistent():
    sim = SimulatorSource(seed=1)
    for point in sim._tick({"AAPL", "GOOGL", "NVDA"}):
        assert math.isclose(point.change, point.price - point.previous_price)
        expected_pct = (point.price - point.previous_price) / point.previous_price * 100
        assert math.isclose(point.change_percent, expected_pct)


def test_repeated_ticks_move_the_price():
    sim = SimulatorSource(seed=1)
    first = sim._tick({"AAPL"})[0]
    second = sim._tick({"AAPL"})[0]
    assert second.previous_price == first.price
    assert second.price != first.price


def test_seeded_simulator_is_deterministic():
    sim_a = SimulatorSource(seed=42)
    sim_b = SimulatorSource(seed=42)

    for _ in range(10):
        points_a = sim_a._tick({"AAPL", "GOOGL", "TSLA"})
        points_b = sim_b._tick({"AAPL", "GOOGL", "TSLA"})
        assert [p.price for p in points_a] == [p.price for p in points_b]


def test_different_seeds_diverge():
    sim_a = SimulatorSource(seed=1)
    sim_b = SimulatorSource(seed=2)
    points_a = sim_a._tick({"AAPL"})
    points_b = sim_b._tick({"AAPL"})
    assert points_a[0].price != points_b[0].price


def test_unknown_ticker_gets_synthesized_generic_config():
    sim = SimulatorSource(seed=1)
    point = sim._tick({"ZZZZ"})[0]
    assert point.previous_price == 100.0
    config = sim._configs["ZZZZ"]
    assert config.sector == "ZZZZ"
    assert config.annual_drift == 0.08
    assert config.annual_volatility == 0.30


def test_synthesized_ticker_does_not_join_an_existing_sector():
    sim = SimulatorSource(seed=1)
    sim._tick({"ZZZZ", "AAPL"})
    assert sim._configs["ZZZZ"].sector != sim._configs["AAPL"].sector


def test_price_never_negative_over_many_ticks():
    sim = SimulatorSource(seed=123)
    for _ in range(2000):
        points = sim._tick({"TSLA"})  # highest volatility default ticker
        assert points[0].price > 0


def test_same_sector_tickers_are_positively_correlated():
    sim = SimulatorSource(seed=7)
    ups_together = 0
    trials = 500
    for _ in range(trials):
        points = {p.ticker: p for p in sim._tick({"AAPL", "GOOGL", "TSLA"})}  # AAPL/GOOGL tech, TSLA auto
        if (points["AAPL"].change > 0) == (points["GOOGL"].change > 0):
            ups_together += 1
    assert ups_together > trials * 0.6  # well above the 50% chance baseline for independent moves


def test_cross_sector_correlation_is_weaker_than_same_sector():
    sim_same = SimulatorSource(seed=7)
    sim_cross = SimulatorSource(seed=7)
    trials = 500

    same_sector_agree = 0
    cross_sector_agree = 0
    for _ in range(trials):
        same = {p.ticker: p for p in sim_same._tick({"AAPL", "MSFT"})}  # both tech
        if (same["AAPL"].change > 0) == (same["MSFT"].change > 0):
            same_sector_agree += 1

        cross = {p.ticker: p for p in sim_cross._tick({"AAPL", "TSLA"})}  # tech vs auto
        if (cross["AAPL"].change > 0) == (cross["TSLA"].change > 0):
            cross_sector_agree += 1

    assert same_sector_agree > cross_sector_agree


def test_event_jump_applies_expected_magnitude(monkeypatch):
    monkeypatch.setattr("market_data.simulator.EVENT_PROBABILITY_PER_TICK", 1.0)
    sim = SimulatorSource(configs=[TickerConfig("AAPL", 100.0, 0.0, 0.0, "tech")], seed=1)
    point = sim._tick({"AAPL"})[0]

    ratio = abs(point.price - point.previous_price) / point.previous_price
    assert EVENT_MAGNITUDE_RANGE[0] <= ratio <= EVENT_MAGNITUDE_RANGE[1]


def test_no_event_when_probability_zero(monkeypatch):
    monkeypatch.setattr("market_data.simulator.EVENT_PROBABILITY_PER_TICK", 0.0)
    sim = SimulatorSource(configs=[TickerConfig("AAPL", 100.0, 0.0, 0.0, "tech")], seed=1)
    point = sim._tick({"AAPL"})[0]
    # zero drift/vol and no event means the price is unchanged this tick
    assert point.price == point.previous_price


def test_dt_years_matches_five_simulated_minutes_per_tick():
    # Independent check against MARKET_SIMULATOR.md's documented value (~5.09e-5),
    # not a re-derivation of the module's own formula.
    assert DT_YEARS == pytest.approx(5.0876e-5, rel=1e-3)


async def test_stream_yields_batches_for_requested_tickers(monkeypatch):
    monkeypatch.setattr("market_data.simulator.asyncio.sleep", _instant_sleep)
    sim = SimulatorSource(seed=1)
    gen = sim.stream(lambda: {"AAPL", "GOOGL"})
    try:
        first = await gen.__anext__()
        second = await gen.__anext__()
    finally:
        await gen.aclose()

    assert {p.ticker for p in first} == {"AAPL", "GOOGL"}
    assert {p.ticker for p in second} == {"AAPL", "GOOGL"}


async def test_stream_reflects_ticker_set_changes_between_cycles(monkeypatch):
    monkeypatch.setattr("market_data.simulator.asyncio.sleep", _instant_sleep)
    sim = SimulatorSource(seed=1)
    tickers = {"AAPL"}
    gen = sim.stream(lambda: tickers)
    try:
        first = await gen.__anext__()
        assert {p.ticker for p in first} == {"AAPL"}

        tickers = {"AAPL", "MSFT"}
        second = await gen.__anext__()
        assert {p.ticker for p in second} == {"AAPL", "MSFT"}
    finally:
        await gen.aclose()


async def _instant_sleep(_seconds: float) -> None:
    return None
