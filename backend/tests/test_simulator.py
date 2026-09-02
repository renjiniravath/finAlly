import math

import pytest

from market_data.base import PricePoint
from market_data.simulator import (
    DEFAULT_TICKERS,
    EVENT_MAGNITUDE_RANGE,
    SimulatorSource,
    TickerConfig,
)


def test_tick_returns_a_price_point_per_requested_ticker():
    sim = SimulatorSource(seed=42)
    points = sim._tick({"AAPL", "GOOGL", "MSFT"})
    assert {p.ticker for p in points} == {"AAPL", "GOOGL", "MSFT"}
    assert all(isinstance(p, PricePoint) for p in points)


def test_tick_computes_change_fields_consistently():
    sim = SimulatorSource(seed=1)
    points = sim._tick({"AAPL"})
    point = points[0]
    assert point.change == pytest.approx(point.price - point.previous_price)
    assert point.change_percent == pytest.approx(point.change / point.previous_price * 100)


def test_first_tick_previous_price_is_seed_price():
    sim = SimulatorSource(seed=42)
    aapl_seed = next(c.seed_price for c in DEFAULT_TICKERS if c.ticker == "AAPL")
    point = sim._tick({"AAPL"})[0]
    assert point.previous_price == aapl_seed


def test_prices_move_between_ticks():
    sim = SimulatorSource(seed=42)
    first = sim._tick({"AAPL"})[0]
    second = sim._tick({"AAPL"})[0]
    assert first.price != second.price
    # the second tick's "previous" is the first tick's resulting price
    assert second.previous_price == first.price


def test_same_seed_produces_identical_price_path():
    sim_a = SimulatorSource(seed=42)
    sim_b = SimulatorSource(seed=42)

    path_a = [sim_a._tick({"AAPL"})[0].price for _ in range(10)]
    path_b = [sim_b._tick({"AAPL"})[0].price for _ in range(10)]

    assert path_a == path_b


def test_different_seeds_produce_different_price_paths():
    sim_a = SimulatorSource(seed=1)
    sim_b = SimulatorSource(seed=2)

    path_a = [sim_a._tick({"AAPL"})[0].price for _ in range(10)]
    path_b = [sim_b._tick({"AAPL"})[0].price for _ in range(10)]

    assert path_a != path_b


def test_prices_stay_positive_over_many_ticks():
    sim = SimulatorSource(seed=7)
    tickers = {c.ticker for c in DEFAULT_TICKERS}
    for _ in range(1000):
        points = sim._tick(tickers)
        assert all(p.price > 0 for p in points)


def test_unknown_ticker_gets_synthesized_config_with_own_sector():
    sim = SimulatorSource(seed=3)
    sim._tick({"ZZZZ"})
    config = sim._configs["ZZZZ"]
    assert config.sector == "ZZZZ"
    assert config.seed_price == 100.0


def test_unknown_ticker_config_is_only_synthesized_once():
    sim = SimulatorSource(seed=3)
    sim._tick({"ZZZZ"})
    first_config = sim._configs["ZZZZ"]
    sim._tick({"ZZZZ"})
    assert sim._configs["ZZZZ"] is first_config


def test_same_sector_tickers_are_correlated():
    # AAPL/GOOGL/MSFT are all "tech"; TSLA is "auto" (see DEFAULT_TICKERS).
    sim = SimulatorSource(seed=7)
    ups_together_same_sector = 0
    ups_together_diff_sector = 0
    n = 500
    for _ in range(n):
        points = {p.ticker: p for p in sim._tick({"AAPL", "GOOGL", "TSLA"})}
        if (points["AAPL"].change > 0) == (points["GOOGL"].change > 0):
            ups_together_same_sector += 1
        if (points["AAPL"].change > 0) == (points["TSLA"].change > 0):
            ups_together_diff_sector += 1

    # Same-sector tickers should agree on direction well above the ~50% chance baseline.
    assert ups_together_same_sector > n * 0.6
    # Cross-sector agreement should be closer to chance than same-sector agreement.
    assert ups_together_same_sector > ups_together_diff_sector


def test_event_jump_applied_when_probability_forced_to_one(monkeypatch):
    monkeypatch.setattr("market_data.simulator.EVENT_PROBABILITY_PER_TICK", 1.0)
    sim = SimulatorSource(configs=[TickerConfig("AAPL", 190.0, 0.0, 0.0, "tech")], seed=1)

    point = sim._tick({"AAPL"})[0]

    # With zero drift/vol, the GBM step alone would leave price unchanged (190.0); the forced
    # event jump is the only source of movement, so the observed change must fall in its range.
    magnitude = abs(point.change) / point.previous_price
    assert EVENT_MAGNITUDE_RANGE[0] <= magnitude <= EVENT_MAGNITUDE_RANGE[1]


def test_no_event_jump_when_probability_is_zero(monkeypatch):
    monkeypatch.setattr("market_data.simulator.EVENT_PROBABILITY_PER_TICK", 0.0)
    sim = SimulatorSource(configs=[TickerConfig("AAPL", 190.0, 0.0, 0.0, "tech")], seed=1)

    point = sim._tick({"AAPL"})[0]

    # Zero drift and zero volatility with no event means the price is exactly unchanged.
    assert point.price == pytest.approx(190.0)


def test_weights_satisfy_unit_variance_identity():
    from market_data.simulator import W_IDIO, W_MARKET, W_SECTOR

    assert math.isclose(W_MARKET**2 + W_SECTOR**2 + W_IDIO**2, 1.0, rel_tol=1e-9)


async def test_stream_yields_batches_using_get_tickers_callable():
    sim = SimulatorSource(seed=42)
    calls = {"count": 0}

    def get_tickers() -> set[str]:
        calls["count"] += 1
        return {"AAPL"}

    agen = sim.stream(get_tickers)
    first_batch = await agen.__anext__()
    await agen.aclose()

    assert calls["count"] == 1
    assert first_batch[0].ticker == "AAPL"
