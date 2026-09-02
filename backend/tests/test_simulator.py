import pytest

from market_data import simulator as simulator_module
from market_data.simulator import DEFAULT_TICKERS, SimulatorSource


def test_tick_returns_price_point_for_each_requested_ticker():
    sim = SimulatorSource(seed=42)
    points = sim._tick({"AAPL", "GOOGL"})
    tickers = {p.ticker for p in points}
    assert tickers == {"AAPL", "GOOGL"}


def test_tick_moves_price_away_from_previous():
    sim = SimulatorSource(seed=42)
    point = sim._tick({"AAPL"})[0]
    assert point.previous_price == 190.0  # AAPL seed price
    assert point.price != point.previous_price


def test_price_point_change_fields_are_consistent():
    sim = SimulatorSource(seed=42)
    point = sim._tick({"AAPL"})[0]
    assert point.change == pytest.approx(point.price - point.previous_price)
    assert point.change_percent == pytest.approx(point.change / point.previous_price * 100)


def test_same_seed_produces_identical_price_path():
    sim1 = SimulatorSource(seed=123)
    sim2 = SimulatorSource(seed=123)
    for _ in range(10):
        prices1 = [p.price for p in sim1._tick({"AAPL", "GOOGL", "TSLA"})]
        prices2 = [p.price for p in sim2._tick({"AAPL", "GOOGL", "TSLA"})]
        assert prices1 == prices2


def test_different_seeds_diverge():
    sim1 = SimulatorSource(seed=1)
    sim2 = SimulatorSource(seed=2)
    price1 = sim1._tick({"AAPL"})[0].price
    price2 = sim2._tick({"AAPL"})[0].price
    assert price1 != price2


def test_prices_stay_positive_over_many_ticks():
    sim = SimulatorSource(seed=7)
    tickers = {c.ticker for c in DEFAULT_TICKERS}
    for _ in range(1000):
        points = sim._tick(tickers)
        assert all(p.price > 0 for p in points)


def test_second_tick_uses_first_ticks_price_as_previous():
    sim = SimulatorSource(seed=42)
    first = sim._tick({"AAPL"})[0]
    second = sim._tick({"AAPL"})[0]
    assert second.previous_price == first.price


def test_unknown_ticker_gets_synthesized_config_with_seed_price_100():
    sim = SimulatorSource(seed=42)
    point = sim._tick({"PYPL"})[0]
    assert point.previous_price == 100.0
    config = sim._configs["PYPL"]
    assert config.seed_price == 100.0
    assert config.annual_drift == 0.08
    assert config.annual_volatility == 0.30


def test_synthesized_config_has_standalone_sector():
    config = SimulatorSource._synthesize_config("XYZ")
    assert config.sector == "XYZ"


def test_synthesized_ticker_is_remembered_across_ticks():
    sim = SimulatorSource(seed=42)
    sim._tick({"PYPL"})
    assert "PYPL" in sim._configs
    # a second tick reuses the same config rather than re-synthesizing a new seed price
    second = sim._tick({"PYPL"})[0]
    assert second.previous_price != 100.0  # moved from the seed after tick 1


def test_same_sector_tickers_agree_on_direction_more_than_different_sector():
    sim = SimulatorSource(seed=7)
    same_sector_agreement = 0
    diff_sector_agreement = 0
    n = 500
    for _ in range(n):
        points = {p.ticker: p for p in sim._tick({"AAPL", "GOOGL", "TSLA"})}
        if (points["AAPL"].change > 0) == (points["GOOGL"].change > 0):
            same_sector_agreement += 1
        if (points["AAPL"].change > 0) == (points["TSLA"].change > 0):
            diff_sector_agreement += 1
    # AAPL/GOOGL share the "tech" sector factor; TSLA is "auto" and only shares the
    # market factor with AAPL, so same-sector agreement should clear the ~250/500
    # chance baseline comfortably and beat cross-sector agreement.
    assert same_sector_agreement > 300
    assert same_sector_agreement > diff_sector_agreement


def test_event_jump_produces_a_multi_percent_move(monkeypatch):
    monkeypatch.setattr(simulator_module, "EVENT_PROBABILITY_PER_TICK", 1.0)
    sim = SimulatorSource(seed=99)
    point = sim._tick({"AAPL"})[0]
    # GBM diffusion alone is ~0.2%/tick for AAPL's vol; forcing the event guarantees an
    # additional 2-5% jump, so the total move should clear a couple percent either way.
    assert abs(point.change_percent) >= 1.5
    assert abs(point.change_percent) <= 6.0


def test_event_never_fires_when_probability_zero(monkeypatch):
    monkeypatch.setattr(simulator_module, "EVENT_PROBABILITY_PER_TICK", 0.0)
    sim = SimulatorSource(seed=99)
    for _ in range(200):
        point = sim._tick({"AAPL"})[0]
        assert abs(point.change_percent) < 2.0  # ordinary GBM move, no event jump


async def test_stream_yields_batches_and_can_be_cancelled():
    sim = SimulatorSource(seed=1)

    def get_tickers() -> set[str]:
        return {"AAPL"}

    aiter = sim.stream(get_tickers)
    first = await anext(aiter)
    assert first[0].ticker == "AAPL"
    await aiter.aclose()
