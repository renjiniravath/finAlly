from market_data.simulator import (
    DEFAULT_TICKERS,
    EVENT_MAGNITUDE_RANGE,
    SimulatorSource,
    TickerConfig,
)


def test_tick_returns_a_point_for_every_requested_ticker():
    sim = SimulatorSource(seed=42)
    points = sim._tick({"AAPL", "GOOGL", "TSLA"})
    tickers = {p.ticker for p in points}
    assert tickers == {"AAPL", "GOOGL", "TSLA"}


def test_prices_stay_positive():
    sim = SimulatorSource(seed=42)
    for _ in range(200):
        points = sim._tick({"AAPL", "TSLA"})
        for point in points:
            assert point.price > 0


def test_change_and_change_percent_are_consistent():
    sim = SimulatorSource(seed=42)
    points = sim._tick({"AAPL"})
    point = points[0]
    assert point.change == point.price - point.previous_price
    assert point.change_percent == (point.price - point.previous_price) / point.previous_price * 100


def test_same_seed_produces_the_same_price_path():
    sim_a = SimulatorSource(seed=42)
    sim_b = SimulatorSource(seed=42)
    for _ in range(10):
        points_a = sim_a._tick({"AAPL", "GOOGL"})
        points_b = sim_b._tick({"AAPL", "GOOGL"})
        assert [p.price for p in points_a] == [p.price for p in points_b]


def test_price_moves_between_ticks():
    sim = SimulatorSource(seed=42)
    first = sim._tick({"AAPL"})
    second = sim._tick({"AAPL"})
    assert first[0].price != second[0].price


def test_unknown_ticker_gets_synthesized_config_with_standalone_sector():
    sim = SimulatorSource(seed=42)
    sim._tick({"ZZZZ"})
    config = sim._configs["ZZZZ"]
    assert config.seed_price == 100.0
    assert config.sector == "ZZZZ"


def test_same_sector_tickers_are_correlated():
    sim = SimulatorSource(seed=7)
    ups_together = 0
    trials = 500
    for _ in range(trials):
        points = {p.ticker: p for p in sim._tick({"AAPL", "GOOGL", "TSLA"})}  # AAPL/GOOGL tech, TSLA auto
        if (points["AAPL"].change > 0) == (points["GOOGL"].change > 0):
            ups_together += 1
    assert ups_together > 300  # well above the ~250 chance baseline


def test_different_sector_tickers_are_less_correlated_than_same_sector():
    sim_tech = SimulatorSource(seed=13)
    tech_agree = 0
    sim_cross = SimulatorSource(seed=13)
    cross_agree = 0
    trials = 500
    for _ in range(trials):
        tech_points = {p.ticker: p for p in sim_tech._tick({"AAPL", "GOOGL"})}
        if (tech_points["AAPL"].change > 0) == (tech_points["GOOGL"].change > 0):
            tech_agree += 1

        cross_points = {p.ticker: p for p in sim_cross._tick({"AAPL", "TSLA"})}
        if (cross_points["AAPL"].change > 0) == (cross_points["TSLA"].change > 0):
            cross_agree += 1

    assert tech_agree > cross_agree


def test_event_jump_magnitude_within_configured_range(monkeypatch):
    import market_data.simulator as simulator_module

    monkeypatch.setattr(simulator_module, "EVENT_PROBABILITY_PER_TICK", 1.0)
    sim = SimulatorSource(seed=1)
    previous_price = sim._prices["AAPL"]
    points = sim._tick({"AAPL"})
    point = next(p for p in points if p.ticker == "AAPL")
    pct_move = abs(point.price - previous_price) / previous_price
    # GBM diffusion alone is ~0.2%; forcing the event guarantees an extra 2-5% jump component
    assert pct_move > EVENT_MAGNITUDE_RANGE[0] - 0.01


def test_default_tickers_cover_the_plan_seed_list():
    tickers = {c.ticker for c in DEFAULT_TICKERS}
    assert tickers == {"AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX"}


def test_custom_configs_are_respected():
    custom = [TickerConfig("FOO", 50.0, 0.0, 0.1, "custom")]
    sim = SimulatorSource(configs=custom, seed=1)
    points = sim._tick({"FOO"})
    assert points[0].previous_price == 50.0
