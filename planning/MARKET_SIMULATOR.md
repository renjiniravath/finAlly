# Market Simulator Design

Design for `SimulatorSource`, the default `MarketDataSource` implementation (`MARKET_INTERFACE.md` §6), used
whenever `MASSIVE_API_KEY` is not set. Lives at `backend/market_data/simulator.py`.

Goal: prices that look and feel alive on a trading-terminal UI — visible per-tick movement, tickers in the same
sector drifting together, the occasional dramatic spike — without pulling in a dependency (numpy, a stats
library) or an amount of code disproportionate to what a demo simulator needs.

## 1. The Math: Geometric Brownian Motion

Each tick, a ticker's price evolves under standard GBM:

```
S(t+dt) = S(t) * exp( (mu - sigma^2 / 2) * dt + sigma * sqrt(dt) * Z )
```

- `mu` — annualized drift (expected return)
- `sigma` — annualized volatility
- `dt` — the tick's time step, expressed in *years* (see §2)
- `Z` — a draw from the standard normal distribution, `N(0, 1)`

This is the standard discretization of GBM (log-normal price, no negative prices, volatility scales with
`sqrt(dt)`) — nothing exotic, and it's the same model real quant tooling uses for toy price paths.

## 2. Time Scale: Compressing a Trading Day into Real Seconds

Ticks fire every 500ms of wall-clock time (`PLAN.md` §6). If `dt` in the formula above were *actual* elapsed
time (0.5s expressed as a fraction of a year), moves would be microscopic — `sigma * sqrt(dt)` for a 30%-vol
stock over 0.5 real seconds is on the order of 0.001%, invisible on screen.

Instead, each 500ms tick simulates a compressed chunk of trading time: **5 simulated minutes per tick.**

```python
TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600   # 252 trading days, 6.5h sessions
SIMULATED_SECONDS_PER_TICK = 5 * 60           # each 500ms tick = 5 simulated minutes
DT_YEARS = SIMULATED_SECONDS_PER_TICK / TRADING_SECONDS_PER_YEAR   # ≈ 5.09e-5
```

At `sigma = 0.30`, that gives `sigma * sqrt(DT_YEARS) ≈ 0.0021` — roughly a 0.2% per-tick standard deviation,
which reads as a believable, visibly-flickering price on the watchlist without being absurd. This is a
deliberate simplification worth stating explicitly: the simulator does not run in real trading time, it runs
~600x faster (5 sim-minutes per 0.5 real-seconds), so that a demo session shows a full trading day's worth of
character in a few real minutes.

## 3. Correlated Moves: A Simple Factor Model

`PLAN.md` §6 asks for "correlated moves across tickers (e.g., tech stocks move together)." A full covariance
matrix + Cholesky decomposition is the "correct" way to do this and is overkill here — a **one-factor-per-sector
model** gets the same visible effect (tech stocks wobble together, financials wobble together, and there's a
broad market mood everyone leans into a bit) with three normal draws instead of a matrix multiply:

```
shock = W_MARKET * Z_market + W_SECTOR * Z_sector + W_IDIO * Z_idio
```

where `Z_market` is one draw shared by *every* ticker this tick, `Z_sector` is one draw shared by every ticker in
the same sector this tick, and `Z_idio` is a private draw per ticker. Weights are chosen so the variances add up
to 1 (since the three components are independent, variances — not the weights themselves — sum):

```python
W_MARKET = 0.5
W_SECTOR = 0.5
W_IDIO = math.sqrt(1 - W_MARKET**2 - W_SECTOR**2)  # ≈ 0.7071
# 0.5^2 + 0.5^2 + 0.7071^2 = 0.25 + 0.25 + 0.5 = 1.0
```

`shock` is then plugged into the GBM formula in place of the plain `Z`:

```
diffusion = sigma * sqrt(dt) * shock
```

## 4. Random Events

"Occasional random events — sudden 2-5% moves on a ticker for drama" (`PLAN.md` §6): after the GBM step, each
ticker independently has a small chance per tick of an extra jump.

```python
EVENT_PROBABILITY_PER_TICK = 0.0005   # ~0.05% per ticker per tick
EVENT_MAGNITUDE_RANGE = (0.02, 0.05)  # 2-5%
```

At ~120 ticks/minute (500ms cadence) and 10 tickers, that's roughly one event every ~17 minutes of demo time
across the whole watchlist — noticeable during a session, not constant noise. The direction (up/down) is a coin
flip; the magnitude is a uniform draw from the range, applied as a straight multiplicative jump:
`price *= 1 + sign * magnitude`.

## 5. Seed Data

Matches `PLAN.md` §7's ten default tickers, with sector groupings for the factor model and rough real-world
seed prices/vol so the demo feels grounded:

```python
@dataclass(frozen=True, slots=True)
class TickerConfig:
    ticker: str
    seed_price: float
    annual_drift: float
    annual_volatility: float
    sector: str


DEFAULT_TICKERS: list[TickerConfig] = [
    TickerConfig("AAPL",  190.0, 0.08, 0.28, "tech"),
    TickerConfig("GOOGL", 175.0, 0.08, 0.30, "tech"),
    TickerConfig("MSFT",  420.0, 0.08, 0.26, "tech"),
    TickerConfig("AMZN",  185.0, 0.10, 0.32, "tech"),
    TickerConfig("NVDA",  130.0, 0.15, 0.45, "tech"),
    TickerConfig("META",  500.0, 0.10, 0.35, "tech"),
    TickerConfig("TSLA",  250.0, 0.05, 0.55, "auto"),
    TickerConfig("JPM",   210.0, 0.06, 0.22, "financial"),
    TickerConfig("V",     280.0, 0.07, 0.20, "financial"),
    TickerConfig("NFLX",  650.0, 0.09, 0.33, "media"),
]
```

Six of the ten tickers share the `tech` sector factor (per the PLAN's own example), so that grouping is where
the correlation is most visible; `auto`, `financial`, and `media` each currently have one or two members, which
is fine — the sector factor just contributes less visible "move together" effect until/unless more tickers are
added to those sectors via the watchlist. If a user adds a ticker via the watchlist/AI chat that isn't in
`DEFAULT_TICKERS`, it gets a synthesized config (see §7).

## 6. Code Structure

`DEFAULT_TICKERS` from §5 lives in the same module as the class below and is imported/used directly (elided
from this snippet for brevity).

```python
# backend/market_data/simulator.py
import asyncio
import math
import random
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from .base import MarketDataSource, PricePoint

TICK_SECONDS = 0.5
TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600
SIMULATED_SECONDS_PER_TICK = 5 * 60
DT_YEARS = SIMULATED_SECONDS_PER_TICK / TRADING_SECONDS_PER_YEAR

W_MARKET = 0.5
W_SECTOR = 0.5
W_IDIO = math.sqrt(1 - W_MARKET**2 - W_SECTOR**2)

EVENT_PROBABILITY_PER_TICK = 0.0005
EVENT_MAGNITUDE_RANGE = (0.02, 0.05)


@dataclass(frozen=True, slots=True)
class TickerConfig:
    ticker: str
    seed_price: float
    annual_drift: float
    annual_volatility: float
    sector: str


class SimulatorSource(MarketDataSource):
    def __init__(self, configs: list[TickerConfig] = DEFAULT_TICKERS, seed: int | None = None) -> None:
        self._configs: dict[str, TickerConfig] = {c.ticker: c for c in configs}
        self._prices: dict[str, float] = {c.ticker: c.seed_price for c in configs}
        self._rng = random.Random(seed)

    async def stream(
        self, get_tickers: Callable[[], set[str]]
    ) -> AsyncIterator[list[PricePoint]]:
        while True:
            yield self._tick(get_tickers())
            await asyncio.sleep(TICK_SECONDS)

    def _tick(self, tickers: set[str]) -> list[PricePoint]:
        for ticker in tickers:
            self._configs.setdefault(ticker, self._synthesize_config(ticker))
            self._prices.setdefault(ticker, self._configs[ticker].seed_price)

        sectors = {self._configs[t].sector for t in tickers}
        z_market = self._rng.gauss(0, 1)
        z_sector = {s: self._rng.gauss(0, 1) for s in sectors}

        now = datetime.now(UTC)
        points = []
        for ticker in tickers:
            config = self._configs[ticker]
            previous = self._prices[ticker]

            z_idio = self._rng.gauss(0, 1)
            shock = W_MARKET * z_market + W_SECTOR * z_sector[config.sector] + W_IDIO * z_idio
            drift = (config.annual_drift - 0.5 * config.annual_volatility**2) * DT_YEARS
            diffusion = config.annual_volatility * math.sqrt(DT_YEARS) * shock
            price = previous * math.exp(drift + diffusion)

            if self._rng.random() < EVENT_PROBABILITY_PER_TICK:
                magnitude = self._rng.uniform(*EVENT_MAGNITUDE_RANGE)
                sign = self._rng.choice((-1, 1))
                price *= 1 + sign * magnitude

            self._prices[ticker] = price
            points.append(
                PricePoint(
                    ticker=ticker,
                    price=price,
                    previous_price=previous,
                    change=price - previous,
                    change_percent=(price - previous) / previous * 100,
                    timestamp=now,
                )
            )
        return points

    @staticmethod
    def _synthesize_config(ticker: str) -> TickerConfig:
        """A ticker added at runtime via the watchlist that isn't in DEFAULT_TICKERS gets
        reasonable generic parameters and its own standalone sector, so it doesn't fake a
        correlation with an unrelated group."""
        return TickerConfig(ticker, seed_price=100.0, annual_drift=0.08, annual_volatility=0.30, sector=ticker)
```

Notes on the code:
- One `random.Random` instance per simulator, constructor-injectable `seed` — deterministic runs for unit tests
  (`SimulatorSource(seed=42)` produces the same price path every time), real randomness in production
  (`seed=None`).
- `_tick` draws `z_market` once and one `z_sector` per *distinct sector present this tick* — not one per ticker
  — which is what makes tickers in the same sector move together rather than just individually noisy.
- A ticker outside `DEFAULT_TICKERS` (added via the watchlist or AI chat) gets a synthesized config with its own
  sector name equal to its ticker — i.e., no sector-mates, so it doesn't spuriously correlate with an unrelated
  group. `seed_price=100.0` is an arbitrary but plausible starting point since we don't know its real price
  without calling Massive (which, by definition, isn't configured when the simulator is active).
- No numpy: `random.gauss` is the standard-library normal draw and is all this needs — matches the project's
  "don't overengineer" guidance and keeps `backend/pyproject.toml` free of a numeric dependency that exists only
  for this.

## 7. Testability

Because `_tick` is a pure function of `(rng state, current prices, requested tickers)`, unit tests can:
- Seed the RNG and assert the exact resulting price sequence for a single ticker (verifies the GBM formula).
- Feed the same `tickers` set across several ticks and assert two same-sector tickers' price changes are
  positively correlated over many ticks (verifies the factor model), while two different-sector tickers are not.
- Force `EVENT_PROBABILITY_PER_TICK` to `1.0` in a test-only instance and assert a jump of the expected magnitude
  range occurs.

No wall-clock dependency needs mocking for any of this — only `SimulatorSource.stream()`'s `asyncio.sleep` loop
touches real time, and that's a thin wrapper around `_tick` that tests can bypass by calling `_tick` directly.
