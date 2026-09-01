# Massive API Reference

Research notes on the [Massive](https://massive.com) market data API (the 2026 rebrand of Polygon.io) for the
endpoints relevant to FinAlly: real-time-ish quotes for a *set* of tickers, and end-of-day (EOD) bars. This is
reference material for `MARKET_INTERFACE.md`, which defines the Python interface our backend actually uses.

## 1. Account & Authentication

- Sign up and get a key at the [Massive dashboard](https://massive.com/dashboard/keys).
- The API is accessed at base URL **`https://api.massive.com`** (the historical `api.polygon.io` host still
  routes the same traffic, but new integrations should target `api.massive.com`).
- Every request authenticates with the key as a **Bearer token**:

  ```
  Authorization: Bearer <MASSIVE_API_KEY>
  ```

  Older Polygon-style `?apiKey=...` query param auth also still works, but the header form is what the official
  client uses and is what we use in this project.
- FinAlly reads the key from the `MASSIVE_API_KEY` environment variable (see `planning/PLAN.md` §5). This is also
  the exact env var name the official `massive` Python client looks for by default.

## 2. Rate Limits

| Tier | Limit |
|---|---|
| Free | 5 requests / minute |
| Any paid tier | No hard cap; Massive asks integrators to stay under ~100 requests/second |

This is why `PLAN.md` specifies a 15-second REST poll interval on the free tier: one call per 15s = 4 calls/min,
safely under the 5/min ceiling, *provided each poll fetches every watched ticker in a single request* (§4 below)
rather than one request per ticker.

A `429` response means the limit was exceeded; back off and retry on the next scheduled poll rather than
retrying immediately.

## 3. Official Python Client

Massive publishes an official client, `massive`, covering both REST and WebSocket APIs.

```bash
uv add massive
```

```python
from massive import RESTClient

client = RESTClient(api_key="...")  # or omit api_key to read MASSIVE_API_KEY from the environment
```

The client is **synchronous** (built on `urllib3`), so calls made from FastAPI's async event loop must be
wrapped in `asyncio.to_thread(...)` — see `MARKET_INTERFACE.md`.

It raises `massive.exceptions.AuthError` for a missing/invalid key and `massive.exceptions.BadResponse` for any
non-200 response. It retries `413/429/499/500/502/503/504` internally with backoff before raising.

## 4. Endpoint: Snapshot for Multiple Tickers (real-time-ish)

This is the endpoint FinAlly's Massive-backed data source polls on a timer. One call returns the latest trade,
latest quote, and today's OHLC bar for every ticker requested — exactly the "poll the union of watched tickers"
pattern `PLAN.md` §6 calls for.

**Raw REST**

```
GET /v2/snapshot/locale/us/markets/stocks/tickers?tickers=AAPL,GOOGL,MSFT
Authorization: Bearer <MASSIVE_API_KEY>
```

`tickers` is a comma-separated list (no documented hard cap for this endpoint, but keep requests to the watchlist
∪ open-positions union, which is at most a few dozen tickers in this app). Omit `tickers` to get every US ticker
(not needed here).

Example response (trimmed to one ticker):

```json
{
  "status": "OK",
  "count": 1,
  "tickers": [
    {
      "ticker": "AAPL",
      "todaysChange": 1.23,
      "todaysChangePerc": 0.65,
      "updated": 1736870400123456789,
      "day":  { "o": 189.5, "h": 191.2, "l": 189.0, "c": 190.7, "v": 41200000, "vw": 190.1 },
      "prevDay": { "o": 188.0, "h": 190.0, "l": 187.5, "c": 189.47, "v": 55000000, "vw": 188.9 },
      "lastTrade": { "p": 190.73, "s": 100, "t": 1736870400123456789 },
      "lastQuote": { "P": 190.75, "p": 190.71, "t": 1736870400123456789 },
      "min": { "o": 190.6, "h": 190.8, "l": 190.5, "c": 190.73, "v": 12000, "t": 1736870400000 }
    }
  ]
}
```

Field notes:
- `day.c` — current/latest daily close-so-far; this is the "current price" FinAlly displays.
- `prevDay.c` — yesterday's close; used to compute the daily change if `todaysChange`/`todaysChangePerc` aren't
  present for a ticker with no trades yet today.
- `updated` — nanosecond Unix timestamp of the last update backing this snapshot.
- **Free tier note**: snapshot data is delayed ~15 minutes on the free tier; only paid tiers get true real-time
  values. Either way the shape of the response is identical, so this doesn't affect our interface design — only
  how "live" the numbers are.
- Snapshot data resets at midnight ET and populates as exchanges open (as early as 4am ET), so pre-market hours
  can return partially-empty `day` objects.

**Official client**

```python
from massive import RESTClient

client = RESTClient(api_key="...")

snapshots = client.get_snapshot_all(
    market_type="stocks",
    tickers=["AAPL", "GOOGL", "MSFT"],
)

for s in snapshots:
    print(s.ticker, s.day.close, s.todays_change_percent, s.updated)
```

`get_snapshot_all` deserializes into a list of `TickerSnapshot` objects with `.day`, `.prev_day`, `.last_trade`,
`.last_quote`, `.todays_change`, `.todays_change_percent`, `.updated` attributes mirroring the JSON above
(snake_cased).

There is also a `GET /v3/snapshot` "unified" endpoint (`client.list_universal_snapshots(ticker_any_of=[...])`)
that spans stocks/options/forex/crypto in one call. We don't need multi-asset-class support, so the simpler,
stocks-only `get_snapshot_all` is the better fit — one purpose-built endpoint beats a general one we'd have to
filter.

## 5. Endpoint: Previous Close / End-of-Day Bar

Two shapes matter for EOD data:

### 5a. One ticker's previous session

```
GET /v2/aggs/ticker/AAPL/prev?adjusted=true
```

```json
{
  "status": "OK",
  "ticker": "AAPL",
  "resultsCount": 1,
  "results": [
    { "T": "AAPL", "o": 188.0, "h": 190.0, "l": 187.5, "c": 189.47, "v": 55000000, "vw": 188.9, "t": 1736784000000 }
  ]
}
```

```python
prev = client.get_previous_close_agg(ticker="AAPL")
print(prev.close, prev.volume, prev.timestamp)
```

One ticker per call — fine for occasional lookups, not for populating a whole watchlist (that's what §4's
snapshot endpoint is for, since its `prevDay` field already carries this same data for every ticker in one
request).

### 5b. Every US ticker for one date (grouped daily bars)

```
GET /v2/aggs/grouped/locale/us/market/stocks/2026-08-31?adjusted=true
```

Returns OHLCV for the entire US stock market on that date in one response — useful for backfilling historical
EOD data (e.g. seeding a demo chart with real closes) but not needed for the live polling path.

```python
for bar in client.get_grouped_daily_aggs(date="2026-08-31", adjusted=True):
    ...  # bar.ticker, bar.open, bar.close, bar.volume, ...
```

### 5c. Single ticker/date open, close, pre/post market

```
GET /v1/open-close/AAPL/2026-08-31
```

```json
{ "symbol": "AAPL", "from": "2026-08-31", "open": 324.66, "close": 325.12, "high": 326.2, "low": 322.3,
  "preMarket": 324.5, "afterHours": 322.1, "volume": 26122646, "status": "OK" }
```

Only useful if we ever want pre/post-market prices specifically; not used by FinAlly's core flows.

## 6. WebSocket Streaming (not used by FinAlly, noted for completeness)

Massive also offers a WebSocket API (`client.WebSocketClient`, subscriptions like `["Q.AAPL", "T.AAPL"]` for
quotes/trades) for push-based real-time data. `PLAN.md` §3 deliberately chooses **REST polling** over WebSockets
for the Massive integration to keep the market-data layer symmetric with the simulator (both are "a background
task calls something on a timer and writes to a cache") and to avoid a second class of connection-lifecycle bugs
on top of the SSE connection we already manage to the browser. Documented here only so a future contributor
doesn't have to re-discover that WebSockets were a deliberate non-choice, not an oversight.

## 7. Error Handling

| HTTP status | Meaning | Handling |
|---|---|---|
| 200 | OK | normal |
| 401/403 | bad/missing API key | fatal at startup — fail loudly, don't fall back to the simulator silently |
| 429 | rate limited | log and skip this poll cycle; try again next tick |
| 5xx | Massive-side error | log and skip this poll cycle; keep serving last-known prices from the cache |

All of these should go through standard structured logging per `PLAN.md` §8 — no separate tracing/alerting
infrastructure needed at this scale.

## Sources

- [Overview | Stocks REST API - Massive](https://massive.com/docs/rest/stocks/overview)
- [Unified Snapshot](https://massive.com/docs/rest/stocks/snapshots/unified-snapshot.md)
- [Previous Day Bar (OHLC)](https://massive.com/docs/rest/stocks/aggregates/previous-day-bar.md)
- [Daily Market Summary (OHLC)](https://massive.com/docs/rest/stocks/aggregates/daily-market-summary.md)
- [Daily Ticker Summary (OHLC)](https://massive.com/docs/rest/stocks/aggregates/daily-ticker-summary.md)
- [What is the request limit for Massive's RESTful APIs?](https://massive.com/knowledge-base/article/what-is-the-request-limit-for-massives-restful-apis)
- [massive-com/client-python](https://github.com/massive-com/client-python) (source read directly: `rest/snapshot.py`, `rest/aggs.py`, `rest/base.py`, `rest/__init__.py`, `rest/models/snapshot.py`, `rest/models/aggs.py`, `exceptions.py`)
