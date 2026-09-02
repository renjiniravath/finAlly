# Market Data Backend — Detailed Design

Consolidated, implementation-ready design for `backend/market_data/` and its integration points in the FastAPI
app. This document synthesizes and extends `MARKET_INTERFACE.md`, `MARKET_SIMULATOR.md`, and `MASSIVE_API.md`
into one place with everything needed to actually write the code: the unified interface, both implementations,
the shared cache, the SSE endpoint, startup wiring, configuration, error handling, and tests. Where a source
document already goes deeper on rationale (e.g., *why* GBM, *why* REST over WebSocket), this doc links back
to it rather than re-litigating; the goal here is a build-order walkthrough with complete code.

Per `PLAN.md` §6, the entire market data layer is env-driven and source-agnostic downstream: SSE streaming,
trade execution, and the frontend never know or care whether prices come from the simulator or Massive.

---

## 1. Goals & Non-Goals

**Goals**
- One abstract interface (`MarketDataSource`) that both the simulator and Massive client implement identically.
- A single shared, in-memory price cache that is the *only* thing downstream code reads from.
- Prices for the union of watchlist tickers and open positions, kept live even if a position's ticker is
  removed from the watchlist.
- Env-var-driven source selection with zero other configuration required to run (`MASSIVE_API_KEY` unset →
  simulator works out of the box).
- Deterministic, testable behavior for both implementations without network or wall-clock dependencies in tests.

**Non-Goals**
- No WebSocket streaming to Massive (REST polling only — keeps both sources symmetric; see
  `MASSIVE_API.md` §6).
- No multi-source aggregation or failover between simulator and Massive — exactly one is active per run.
- No per-ticker on-demand fetch method — trade execution and the frontend always read the cache, never the
  source directly (`MARKET_INTERFACE.md` §7).
- No persistence of price history beyond what the frontend accumulates client-side from the SSE stream
  (`PLAN.md` §2) and what `portfolio_snapshots` records (`PLAN.md` §7) — the price cache itself is not durable
  and rebuilds on restart.

---

## 2. Directory Structure

```
backend/market_data/
├── __init__.py
├── base.py            # PricePoint, MarketDataSource (§3)
├── cache.py            # PriceCache (§4)
├── universe.py         # ticker-universe query: watchlist ∪ open positions (§5)
├── simulator.py         # SimulatorSource — GBM + factor model (§6)
├── massive_source.py    # MassiveSource — REST polling client (§7)
└── factory.py           # get_market_data_source() env selection (§8)
```

Consumed from `backend/main.py` (startup wiring, §9) and `backend/api/stream.py` (SSE endpoint, §10).

---

## 3. The Unified Interface

Both implementations are the same shape: **a background loop that, on some cadence, produces a batch of price
updates for whatever tickers we currently care about, forever, until cancelled.** One async generator method
covers this — no factory of sub-clients, no separate single-ticker vs. all-tickers methods.

```python
# backend/market_data/base.py
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class PricePoint:
    ticker: str
    price: float
    previous_price: float
    change: float            # price - previous_price
    change_percent: float    # change / previous_price * 100
    timestamp: datetime


class MarketDataSource(ABC):
    """Produces price updates for a changing set of tickers, forever, until the consuming task is cancelled."""

    @abstractmethod
    def stream(
        self, get_tickers: Callable[[], set[str]]
    ) -> AsyncIterator[list[PricePoint]]:
        """
        Yield batches of PricePoint updates indefinitely.

        `get_tickers` is called at the start of each cycle to get the current ticker universe
        (watchlist ∪ open positions). Callers don't need to restart the stream when the
        watchlist changes — the callable is re-invoked every cycle.
        """
        ...
```

`get_tickers` is a plain callable rather than a value fixed at construction time because the watchlist changes
at runtime via `POST/DELETE /api/watchlist`, and a held position must keep a live price even after its ticker
is removed from the watchlist (`PLAN.md` §6). Re-reading a small in-memory set every cycle is free, so neither
implementation needs restart logic when the set changes.

---

## 4. Shared Price Cache

The cache is the **only** thing that reads from a `MarketDataSource`. The SSE endpoint, trade execution, and
portfolio valuation all read from the cache — never from the source directly. This keeps the request path free
of network calls (Massive) or extra computation (simulator) and gives every consumer a consistent snapshot.

```python
# backend/market_data/cache.py
import asyncio
from .base import PricePoint


class PriceCache:
    def __init__(self) -> None:
        self._prices: dict[str, PricePoint] = {}
        self._lock = asyncio.Lock()

    async def update(self, batch: list[PricePoint]) -> None:
        async with self._lock:
            for point in batch:
                self._prices[point.ticker] = point

    async def get(self, ticker: str) -> PricePoint | None:
        async with self._lock:
            return self._prices.get(ticker)

    async def snapshot(self) -> dict[str, PricePoint]:
        async with self._lock:
            return dict(self._prices)
```

`snapshot()` returns a shallow copy so a caller can iterate it without holding the lock — important for the SSE
endpoint (§10), which may hold a snapshot across an `await` while writing to a slow client socket.

---

## 5. Ticker Universe: Watchlist ∪ Open Positions

Both `MarketDataSource.stream()` and the cache take their ticker set from a single query function, so the
"union" rule lives in exactly one place:

```python
# backend/market_data/universe.py
import aiosqlite


async def watched_tickers(db: aiosqlite.Connection, user_id: str = "default") -> set[str]:
    cursor = await db.execute(
        """
        SELECT ticker FROM watchlist WHERE user_id = ?
        UNION
        SELECT ticker FROM positions WHERE user_id = ? AND quantity > 0
        """,
        (user_id, user_id),
    )
    rows = await cursor.fetchall()
    return {row[0] for row in rows}
```

Because `MarketDataSource.stream()` expects a **synchronous** `Callable[[], set[str]]` (§3) but this query is
async (SQLite I/O), the app maintains a small in-memory mirror that's refreshed cheaply and read synchronously
by the streaming loop — avoiding an `await` inside the source's tight polling loop and avoiding a query per
tick for the simulator's 500ms cadence:

```python
# backend/main.py (sketch, continued in §9)
class TickerUniverse:
    """In-memory mirror of watchlist ∪ open positions, refreshed on every watchlist/trade write."""

    def __init__(self) -> None:
        self._tickers: set[str] = set()

    def get(self) -> set[str]:
        return self._tickers

    async def refresh(self, db: aiosqlite.Connection) -> None:
        self._tickers = await watched_tickers(db)
```

`refresh()` is called once at startup and again after every `POST/DELETE /api/watchlist` and every executed
trade (buy opens a position, sell may close one to zero). `TickerUniverse.get` is what's passed as `get_tickers`
to `source.stream(...)`.

---

## 6. `SimulatorSource` — Default Implementation

Full rationale in `MARKET_SIMULATOR.md`; this section reproduces the complete, ready-to-write module.

### 6.1 Model

Each tick, a ticker's price evolves under **geometric Brownian motion**:

```
S(t+dt) = S(t) * exp( (mu - sigma^2 / 2) * dt + sigma * sqrt(dt) * shock )
```

- `mu` — annualized drift, `sigma` — annualized volatility, `dt` — tick step in *years*.
- `shock` replaces a plain `N(0,1)` draw with a **three-factor blend** so tickers in the same sector move
  together (`PLAN.md` §6's "tech stocks move together"):

```
shock = W_MARKET * Z_market + W_SECTOR * Z_sector[ticker.sector] + W_IDIO * Z_idio
```

`Z_market` is one draw shared by every ticker this tick; `Z_sector` is one draw per distinct sector present
this tick; `Z_idio` is private per ticker. Weights are chosen so **variances** sum to 1 (the three components
are independent):

```python
W_MARKET = 0.5
W_SECTOR = 0.5
W_IDIO = math.sqrt(1 - W_MARKET**2 - W_SECTOR**2)  # ≈ 0.7071
```

### 6.2 Time Compression

Ticks fire every 500ms wall-clock (`PLAN.md` §6), but real elapsed time would produce invisible moves. Each
tick instead simulates **5 minutes of trading time**, so a demo session shows a full day's character in a few
real minutes:

```python
TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600
SIMULATED_SECONDS_PER_TICK = 5 * 60
DT_YEARS = SIMULATED_SECONDS_PER_TICK / TRADING_SECONDS_PER_YEAR   # ≈ 5.09e-5
```

At `sigma = 0.30`, `sigma * sqrt(DT_YEARS) ≈ 0.0021` — a visibly-flickering ~0.2% per-tick move.

### 6.3 Random Events

"Occasional random events — sudden 2-5% moves" (`PLAN.md` §6): after the GBM step, each ticker independently
has a small per-tick chance of an extra multiplicative jump.

```python
EVENT_PROBABILITY_PER_TICK = 0.0005   # ~0.05% per ticker per tick
EVENT_MAGNITUDE_RANGE = (0.02, 0.05)  # 2-5%
```

### 6.4 Seed Data

Matches `PLAN.md` §7's ten default tickers, grouped into sectors for the factor model:

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

### 6.5 Full Module

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
        """A ticker added at runtime that isn't in DEFAULT_TICKERS gets generic parameters and
        its own standalone sector (equal to its ticker), so it doesn't spuriously correlate with
        an unrelated group."""
        return TickerConfig(ticker, seed_price=100.0, annual_drift=0.08, annual_volatility=0.30, sector=ticker)
```

No numpy — `random.gauss` is the standard-library normal draw and is all this needs. One `random.Random`
instance per simulator with a constructor-injectable `seed` gives deterministic unit tests
(`SimulatorSource(seed=42)`) and real randomness in production (`seed=None`).

---

## 7. `MassiveSource` — Real Market Data Implementation

Full API reference in `MASSIVE_API.md`; this section is the complete, ready-to-write module.

### 7.1 Dependency & Auth

```bash
uv add massive
```

```python
from massive import RESTClient

client = RESTClient(api_key="...")   # or omit to read MASSIVE_API_KEY from the environment
```

The client is **synchronous** (`urllib3`-based). Every call from the FastAPI event loop must be pushed off-loop
with `asyncio.to_thread`, or it will stall the SSE stream and every other in-flight request for the duration of
the HTTP call.

### 7.2 Endpoint

One call fetches latest trade/quote/OHLC for every ticker in a single request — the "poll the union of watched
tickers" pattern `PLAN.md` §6 requires:

```python
snapshots = client.get_snapshot_all(market_type="stocks", tickers=["AAPL", "GOOGL", "MSFT"])
for s in snapshots:
    print(s.ticker, s.day.close, s.todays_change_percent, s.updated)
```

Key fields per ticker: `s.day.close` (current/latest daily close-so-far — this is FinAlly's displayed price),
`s.prev_day.close` (yesterday's close, fallback for change calc), `s.todays_change` /
`s.todays_change_percent` (preferred when present).

### 7.3 Rate Limits & Poll Interval

| Tier | Limit | Poll interval used |
|---|---|---|
| Free | 5 requests/min | 15s (4 calls/min — safe margin) |
| Paid | ~100 req/s soft guidance | 15s default; configurable via `MASSIVE_POLL_INTERVAL_SECONDS` |

One request covers every watched ticker, so 15s stays safe on the free tier regardless of watchlist size.

### 7.4 Full Module

```python
# backend/market_data/massive_source.py
import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime

from massive import RESTClient
from massive.exceptions import BadResponse

from .base import MarketDataSource, PricePoint

logger = logging.getLogger(__name__)


class MassiveSource(MarketDataSource):
    def __init__(self, api_key: str, poll_interval_seconds: float = 15.0) -> None:
        self._client = RESTClient(api_key=api_key)
        self._poll_interval = poll_interval_seconds

    async def stream(
        self, get_tickers: Callable[[], set[str]]
    ) -> AsyncIterator[list[PricePoint]]:
        while True:
            tickers = get_tickers()
            if tickers:
                try:
                    yield await asyncio.to_thread(self._fetch, tickers)
                except BadResponse:
                    logger.exception("massive snapshot fetch failed, keeping last known prices")
            await asyncio.sleep(self._poll_interval)

    def _fetch(self, tickers: set[str]) -> list[PricePoint]:
        snapshots = self._client.get_snapshot_all(market_type="stocks", tickers=list(tickers))
        now = datetime.now(UTC)
        points = []
        for s in snapshots:
            if s.day is None or s.prev_day is None or s.day.close is None:
                continue  # pre-market: no trades yet today, keep last known price in the cache
            price = s.day.close
            previous = s.prev_day.close
            points.append(
                PricePoint(
                    ticker=s.ticker,
                    price=price,
                    previous_price=previous,
                    change=s.todays_change if s.todays_change is not None else price - previous,
                    change_percent=s.todays_change_percent
                    if s.todays_change_percent is not None
                    else (price - previous) / previous * 100,
                    timestamp=now,
                )
            )
        return points
```

### 7.5 Error Handling

| HTTP status | `massive` client behavior | This module's handling |
|---|---|---|
| 200 | returns snapshots | normal |
| 401/403 | raises `AuthError` | **not caught** — surfaces at `MassiveSource.__init__`/first fetch, fails loudly rather than silently degrading |
| 429 | client retries internally, then raises `BadResponse` if still failing | caught, logged, cache keeps last-known prices, retried next cycle |
| 5xx | client retries `413/429/499/500/502/503/504` with backoff, then raises `BadResponse` | same as 429 |

A ticker with no `day`/`prev_day` yet (pre-market) is skipped for that cycle rather than emitting a garbage
`PricePoint` — the cache simply keeps whatever it last had for that ticker (§4). All errors go through standard
structured logging per `PLAN.md` §8 — no separate tracing/alerting infrastructure at this scale.

---

## 8. Source Selection

```python
# backend/market_data/factory.py
import os
from .base import MarketDataSource
from .simulator import SimulatorSource
from .massive_source import MassiveSource


def get_market_data_source() -> MarketDataSource:
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if api_key:
        poll_interval = float(os.environ.get("MASSIVE_POLL_INTERVAL_SECONDS", "15"))
        return MassiveSource(api_key=api_key, poll_interval_seconds=poll_interval)
    return SimulatorSource()
```

Non-empty `MASSIVE_API_KEY` → `MassiveSource`; absent/empty → `SimulatorSource`. No other env var or config
flag controls this, per `PLAN.md` §5.

---

## 9. Startup Wiring

```python
# backend/main.py (sketch)
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from market_data.base import MarketDataSource
from market_data.cache import PriceCache
from market_data.factory import get_market_data_source
from market_data.universe import TickerUniverse


async def run_market_data(source: MarketDataSource, cache: PriceCache, universe: TickerUniverse) -> None:
    async for batch in source.stream(universe.get):
        await cache.update(batch)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.price_cache = PriceCache()
    app.state.ticker_universe = TickerUniverse()
    await app.state.ticker_universe.refresh(app.state.db)

    source = get_market_data_source()
    task = asyncio.create_task(run_market_data(source, app.state.price_cache, app.state.ticker_universe))
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)
```

`app.state.ticker_universe.refresh(db)` is also called (awaited, from the request handler) after every
`POST/DELETE /api/watchlist` and after every trade execution that opens or fully closes a position — this is
the only place the "watchlist ∪ open positions" union (§5) needs to be recomputed.

`AuthError` from a bad `MASSIVE_API_KEY` should be allowed to propagate out of `get_market_data_source()` /
the first fetch during startup rather than being caught — the app should fail to start rather than silently
running the simulator when the user believed they'd configured real data.

---

## 10. SSE Streaming Endpoint

The one API endpoint this design directly powers (`PLAN.md` §8):

```python
# backend/api/stream.py
import asyncio
import json

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

router = APIRouter()

PUSH_INTERVAL_SECONDS = 0.5  # matches the simulator's cadence; Massive updates simply arrive less often


@router.get("/api/stream/prices")
async def stream_prices(request: Request):
    cache = request.app.state.price_cache

    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            snapshot = await cache.snapshot()
            for point in snapshot.values():
                yield {
                    "event": "price",
                    "data": json.dumps(
                        {
                            "ticker": point.ticker,
                            "price": point.price,
                            "previous_price": point.previous_price,
                            "change": point.change,
                            "change_percent": point.change_percent,
                            "timestamp": point.timestamp.isoformat(),
                        }
                    ),
                }
            await asyncio.sleep(PUSH_INTERVAL_SECONDS)

    return EventSourceResponse(event_generator())
```

The endpoint pushes on its **own** fixed cadence (0.5s) regardless of which source is active — it always reads
whatever is currently in the cache. With the simulator this means every push reflects a fresh tick; with
Massive (15s poll) most pushes repeat the same cached values until the next poll lands, which is fine —
`EventSource` on the frontend simply receives a steady heartbeat of current prices, and price-flash animations
only fire when a value actually changes (frontend's responsibility, not this endpoint's).

---

## 11. Testability

Because `SimulatorSource._tick` is a pure function of `(rng state, current prices, requested tickers)`, and
`MassiveSource._fetch` is a pure function of `(HTTP response, requested tickers)`, both are fully testable
without wall-clock or network dependencies:

**Simulator**
```python
def test_gbm_price_path_is_deterministic():
    sim = SimulatorSource(seed=42)
    first = sim._tick({"AAPL"})
    second = sim._tick({"AAPL"})
    assert first[0].price != second[0].price  # moves
    # re-running with the same seed reproduces the exact same sequence

def test_same_sector_tickers_are_correlated():
    sim = SimulatorSource(seed=7)
    ups_together = 0
    for _ in range(500):
        points = {p.ticker: p for p in sim._tick({"AAPL", "GOOGL", "TSLA"})}  # AAPL/GOOGL tech, TSLA auto
        if (points["AAPL"].change > 0) == (points["GOOGL"].change > 0):
            ups_together += 1
    assert ups_together > 300  # well above the ~250 chance baseline

def test_event_jump_magnitude():
    sim = SimulatorSource(seed=1)
    sim._configs["AAPL"] = replace(sim._configs["AAPL"])  # or monkeypatch EVENT_PROBABILITY_PER_TICK to 1.0
```

**Massive** — mock `RESTClient.get_snapshot_all` to return fixture `TickerSnapshot` objects and assert:
- normal snapshot → correct `PricePoint` fields, `change_percent` matches `todays_change_percent` when present.
- pre-market snapshot (`day=None`) → ticker skipped, no `PricePoint` emitted.
- `BadResponse` raised → caught, logged, `stream()` doesn't crash, next cycle still runs.

**Cache** — `PriceCache.update` then `.get`/`.snapshot` round-trips correctly; concurrent `update` calls don't
corrupt state (property covered implicitly by the `asyncio.Lock`).

No test needs to mock `asyncio.sleep` or wall-clock time — only `stream()`'s outer loop touches real time, and
it's a thin wrapper around the pure `_tick`/`_fetch` methods that tests call directly.

---

## 12. Configuration Summary

| Env var | Required | Default | Effect |
|---|---|---|---|
| `MASSIVE_API_KEY` | No | unset | Non-empty → `MassiveSource`; unset/empty → `SimulatorSource` |
| `MASSIVE_POLL_INTERVAL_SECONDS` | No | `15` | REST poll cadence for `MassiveSource` (only read when the key is set) |

Everything else in this design — simulator tick cadence, GBM parameters, event probability, seed tickers — is a
module-level constant, not an env var, per `PLAN.md`'s preference for simple, non-configurable demo defaults.

---

## 13. Design Decisions Not to Revisit

- **No per-ticker `get_price(ticker)` on the source interface.** Trade execution and the SSE stream both want
  "the latest known price," which is exactly what the cache holds. A second code path calling Massive
  synchronously during a trade would both violate the "fills at the cached price" rule (`PLAN.md` §8) and
  reintroduce a blocking network call on the request path.
- **No plugin registry / dynamic source loading.** Exactly two implementations, chosen once at startup by one
  env var — a registry solves a problem this project doesn't have.
- **`get_tickers` as a callable, not a static set.** Recreating or restarting the source's task on every
  watchlist change is more moving parts for no benefit; re-reading a small in-memory set every cycle is free.
- **REST polling over WebSocket for Massive.** Keeps both sources symmetric ("a background task calls something
  on a timer and writes to a cache") and avoids a second connection-lifecycle class of bugs on top of the SSE
  connection already managed to the browser. See `MASSIVE_API.md` §6.
- **Fail loudly on bad `MASSIVE_API_KEY`, never silently fall back to the simulator.** A user who configured
  real data and got fake data instead is a worse failure mode than a container that won't start.
