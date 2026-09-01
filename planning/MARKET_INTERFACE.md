# Market Data Interface Design

Defines the Python interface `backend/` uses to get prices, shared by the two implementations described in
`PLAN.md` §6: the **simulator** (default, see `MARKET_SIMULATOR.md`) and the **Massive client** (used when
`MASSIVE_API_KEY` is set, see `MASSIVE_API.md`). Everything downstream — the shared price cache, the SSE stream,
trade execution — talks only to this interface and never to either implementation directly.

Lives at `backend/market_data/`.

## 1. Shape of the Problem

Both implementations are fundamentally the same thing: **a background loop that, on some cadence, produces a
batch of price updates for whatever tickers we currently care about, forever, until cancelled.**

- The simulator produces a batch every ~500ms for every configured ticker (it's in-process, no reason to throttle).
- The Massive client produces a batch every `poll_interval` seconds, for the *current* watchlist ∪ open-positions
  union (an external call, must be efficient with rate limits — see `MASSIVE_API.md` §2).

That's one shape: an async generator that yields `list[PricePoint]`. This is the entire interface — no factory
of sub-clients, no separate "get one ticker" vs "get all tickers" methods. Trade execution reads the latest
price for a single ticker out of the cache (§3), not from the data source directly.

## 2. The Interface

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
        (watchlist ∪ open positions) — callers don't need to restart the stream when the
        watchlist changes.
        """
        ...
```

`get_tickers` is a plain callable (not passed once at construction) because the watchlist changes at runtime via
`POST/DELETE /api/watchlist`, and a held position can keep a ticker "live" after it's removed from the
watchlist (`PLAN.md` §6). Re-reading it every cycle means neither implementation needs restart logic when the
set changes.

Both implementations live under `backend/market_data/`:

```
backend/market_data/
├── base.py         # PricePoint, MarketDataSource (above)
├── simulator.py     # SimulatorSource — see MARKET_SIMULATOR.md
├── massive_source.py  # MassiveSource — below
└── factory.py       # get_market_data_source()
```

## 3. Shared Price Cache

The cache is the only thing that reads from a `MarketDataSource`. Everything else (SSE endpoint, trade
execution, portfolio valuation) reads from the cache — never from the source.

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

A single background task wires a source into the cache at app startup:

```python
# backend/main.py (sketch)
async def run_market_data(source: MarketDataSource, cache: PriceCache, get_tickers) -> None:
    async for batch in source.stream(get_tickers):
        await cache.update(batch)


@app.on_event("startup")
async def startup() -> None:
    source = get_market_data_source()
    asyncio.create_task(run_market_data(source, app.state.price_cache, watched_tickers))
```

Where `watched_tickers()` queries the `watchlist` and `positions` tables for the current ticker union. The SSE
endpoint (`GET /api/stream/prices`) separately reads `cache.snapshot()` / `cache.get()` on its own push cadence
and has no dependency on which `MarketDataSource` is active.

## 4. Selecting an Implementation

```python
# backend/market_data/factory.py
import os
from .base import MarketDataSource
from .simulator import SimulatorSource
from .massive_source import MassiveSource


def get_market_data_source() -> MarketDataSource:
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if api_key:
        return MassiveSource(api_key=api_key)
    return SimulatorSource()
```

This is the entire selection logic per `PLAN.md` §5: non-empty `MASSIVE_API_KEY` → Massive, otherwise
simulator. No other env var or config flag controls this.

## 5. `MassiveSource`

Wraps the official `massive` client (`MASSIVE_API.md` §3–4). One thing matters here: the client is
**synchronous** (`urllib3`), so every call must be pushed off the event loop with `asyncio.to_thread`, or it
will stall the SSE stream and every other in-flight request for however long the HTTP call takes.

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
                continue  # pre-market: no trades yet today
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

Notes:
- `poll_interval_seconds` defaults to 15s (free-tier-safe per `MASSIVE_API.md` §2 — one call covers every
  ticker, so 15s stays well under 5 calls/min). Read from an optional `MASSIVE_POLL_INTERVAL_SECONDS` env var if
  we want paid-tier users to poll faster; default to 15 either way, since guessing the user's tier isn't worth
  the complexity.
- A `BadResponse` (rate limit, 5xx) is logged and swallowed for that cycle — the cache simply keeps serving the
  last known prices until the next successful poll, per `MASSIVE_API.md` §7. An `AuthError` (bad key) is *not*
  caught here; it should surface at startup when the source is first constructed, not get silently swallowed
  cycle after cycle.
- Tickers with no `day`/`prev_day` data yet (pre-market) are skipped for that cycle rather than emitting a
  garbage `PricePoint` — the cache just keeps whatever it last had for that ticker.

## 6. `SimulatorSource`

Same interface, no network calls, no `asyncio.to_thread` needed since it's pure in-process computation. Full
design in `MARKET_SIMULATOR.md`; the shape is:

```python
class SimulatorSource(MarketDataSource):
    async def stream(self, get_tickers):
        while True:
            yield self._tick(get_tickers())
            await asyncio.sleep(0.5)
```

## 7. Why This Interface and Not Something Fancier

- **No per-ticker `get_price(ticker)` method on the source.** Trade execution and the SSE stream both want "the
  latest known price," which is exactly what the cache already holds — adding a second code path that calls out
  to Massive synchronously during a trade would both violate `PLAN.md` §8's "fills at the cached price" rule and
  reintroduce a blocking network call on the request path.
- **No plugin registry / dynamic source loading.** There are exactly two implementations, chosen once at startup
  by one env var. A registry would be solving a problem we don't have.
- **`get_tickers` as a callable, not a static set.** The alternative — recreating the source (or restarting its
  task) every time the watchlist changes — is more moving parts for no benefit, since re-reading a small in-memory
  set every cycle is free.
