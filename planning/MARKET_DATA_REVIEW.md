# Market Data Backend — Code Review

Review of `backend/market_data/` (commit `78dd987`, PR #5 "Add Market Data backend: unified interface,
simulator, Massive client") against `planning/PLAN.md` §6, `MARKET_INTERFACE.md`, `MARKET_SIMULATOR.md`,
`MASSIVE_API.md`, and the consolidated `planning/MARKET_DATA_DESIGN.md`.

## Verdict

**Approve.** The implementation is a near-verbatim match of the design doc, all 40 tests pass, the suite is
fully deterministic (seeded RNG, no wall-clock or network dependency, no flakiness across repeated runs), and
no correctness bugs were found. Two minor, non-blocking items are noted below.

## Test Results

```
uv sync   → resolved cleanly, massive==2.8.0 installed
uv run pytest -v
40 passed in ~1.1s (run twice, identical timing and outcome — no flakiness)
```

All test files exercise real behavior, not implementation details:

| File | Tests | Covers |
|---|---|---|
| `test_base.py` | 3 | `PricePoint` immutability, `MarketDataSource` ABC enforcement |
| `test_cache.py` | 6 | round-trip, overwrite, snapshot isolation (copy semantics), concurrent updates |
| `test_factory.py` | 6 | env-var selection logic (unset/empty/whitespace → simulator; set → Massive), poll-interval default/override |
| `test_massive_source.py` | 10 | snapshot parsing, `todays_change` fallback math, pre-market skip (`day`/`prev_day`/`close` all `None` cases), `BadResponse` recovery, empty-ticker-set skip |
| `test_simulator.py` | 15 | per-ticker output, seed-price start, tick-to-tick movement, seed determinism, positivity over 2000 ticks, change/change_percent consistency, unrecognized-ticker synthesis, same-sector correlation, cross-sector decorrelation, forced event magnitude, zero-probability event absence, streaming, ticker-set changes mid-stream, factor-weight variance identity, default ticker roster |

## Design Fidelity

Implementation matches the design doc closely enough that a diff against the code blocks in
`MARKET_DATA_DESIGN.md` §3, §6.5, §7.4, §8 is essentially whitespace-only. Notably preserved:

- `get_tickers` re-invoked every cycle rather than fixed at construction — confirmed by
  `test_stream_reflects_ticker_set_changes_between_cycles`.
- `MassiveSource` catches only `BadResponse`, not `AuthError` — verified directly: `AuthError` and
  `BadResponse` are sibling `Exception` subclasses in `massive.exceptions`, so a bad API key still propagates
  out of `__init__`/first fetch rather than being silently swallowed, per the "fail loudly" decision in
  `MASSIVE_API.md` §7 and `MARKET_DATA_DESIGN.md` §13.
- `PriceCache.snapshot()` returns a shallow copy (`dict(self._prices)`), confirmed by
  `test_snapshot_is_a_copy_not_a_live_view`.
- Simulator's three-factor shock model, GBM time-compression constants, and event-jump parameters match
  `MARKET_SIMULATOR.md` §2–4 exactly, including the `W_IDIO = sqrt(1 - 0.5² - 0.5²)` derivation
  (`test_weights_sum_to_unit_variance`).
- An out-of-`DEFAULT_TICKERS` symbol gets a synthesized config with its own sector (no spurious correlation),
  confirmed by `test_unknown_ticker_gets_synthesized_config_with_own_sector`.

Out of scope for this PR (correctly not attempted here, per `PLAN.md`'s module boundaries): `universe.py`
(watchlist ∪ positions query), `main.py` startup wiring, and the `/api/stream/prices` SSE endpoint — these
consume `market_data/` but live in the backend API layer, not the market-data module itself.

## Findings

**1. Mutable default argument in `SimulatorSource.__init__` (minor, no observed bug)**

`backend/market_data/simulator.py:47`:
```python
def __init__(self, configs: list[TickerConfig] = DEFAULT_TICKERS, seed: int | None = None) -> None:
```
The classic Python foot-gun (all instances default to the *same* list object) doesn't currently bite because
`TickerConfig` is frozen and `_configs`/`_prices` are copied into fresh per-instance dicts in the constructor —
verified empirically that two `SimulatorSource()` instances get independent `_configs` dicts and that
synthesizing a config for an unknown ticker on one instance never mutates the shared `DEFAULT_TICKERS` module
list. Still, it's the kind of pattern a linter (ruff's `B006`) flags, and it's fragile against a future
maintainer adding `configs.append(...)` somewhere. Low priority; would suggest `configs: list[TickerConfig] |
None = None` with `configs = configs or DEFAULT_TICKERS` if it's ever touched again, but not worth a
change on its own.

**2. No test asserts `AuthError` propagates uncaught (minor test-coverage gap)**

The design explicitly calls out (`MASSIVE_API.md` §7, `MARKET_DATA_DESIGN.md` §7.5) that a bad API key must
fail loudly rather than being caught like `BadResponse`. The code satisfies this (confirmed above by
inspecting the exception hierarchy), but there's no `test_massive_source.py` case that mocks
`get_snapshot_all` to raise `AuthError` and asserts it escapes `stream()`/`_fetch()` uncaught. Since this is a
"the important thing is what does *not* happen" contract, an explicit regression test would guard against a
future refactor accidentally widening the `except` clause (e.g. changing it to `except Exception`). Suggested
addition, not required before merge:

```python
@patch("market_data.massive_source.RESTClient")
async def test_stream_does_not_swallow_auth_error(mock_rest_client):
    mock_client = mock_rest_client.return_value
    mock_client.get_snapshot_all.side_effect = AuthError("bad key")
    source = MassiveSource(api_key="bad-key", poll_interval_seconds=0)
    with pytest.raises(AuthError):
        await anext(source.stream(lambda: {"AAPL"}))
```

## Non-Findings Worth Recording

- Division-by-zero in `change_percent` (`(price - previous) / previous * 100`) if `previous_price` were ever
  `0.0`: not reachable in practice — the simulator's GBM/`math.exp` keeps prices strictly positive by
  construction, and a real ticker's `prevDay.close` from Massive is never legitimately `0`. Not flagged as a
  defect; adding a guard would be the kind of defensive code the project's style guide explicitly says to
  avoid for a case that can't happen.
- The concurrency test (`test_concurrent_updates_do_not_corrupt_state`) doesn't stress true parallel
  interleaving (a single-threaded event loop already serializes coroutines, and each `update()` call touches
  only one ticker), so it doesn't prove the `asyncio.Lock` is load-bearing — but it's a reasonable regression
  test for the cache's round-trip correctness under concurrent callers, and the lock is cheap insurance for
  when `update()` batches grow to touch multiple tickers per call.
- `factory.py`'s `float(os.environ.get("MASSIVE_POLL_INTERVAL_SECONDS", "15"))` raises `ValueError` on an
  unparseable value with no try/except — consistent with the project's "fail loudly, don't guess" guidance
  rather than a bug.

## Process Note

Unrelated to the code itself: the initial `git pull` in this session partially failed mid-checkout because
the sandbox denied writing `.claude/settings.json`, leaving 17 files (including all of `backend/market_data/`)
on disk as untracked leftovers. Verified byte-for-byte identical to `origin/main` before staging them and
completing the fast-forward — no data loss, but noting it in case the same interrupted-pull state recurs.
