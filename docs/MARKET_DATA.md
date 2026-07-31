# ADT Market Data — Phase 2A

## Scope and safety

Phase 2A represents, fetches, validates and stores bounded historical candles.
It supports Binance Spot public market data without credentials. It does not
implement strategies, backtests, orders, paper-trading execution, scheduled
workers or large backfills.

Automated tests never access the network. The only normal commands that contact
Binance are `market-data instruments` and `market-data fetch`; both require an
explicit operator invocation.

## Canonical model

An instrument keeps `BTC/USDT` as its canonical symbol and `BTCUSDT` as its
Binance-native symbol. Every candle includes:

- exchange and market type;
- canonical symbol and configured timeframe;
- aware UTC `open_time` and `close_time`;
- Decimal open, high, low, close, base volume and optional quote volume;
- optional trade count, closed/open state and source identifier.

The key is `(exchange, symbol, timeframe, open_time)`. Float price and volume
values are rejected. OHLC relationships, non-negative volumes and alignment
are domain invariants. The quality validator applies the closed-future rule
against the same injectable clock used by the adapter.

Timeframes are configuration objects, not adapter conditionals. Phase 2A ships
`1m`, `5m`, `15m`, `30m`, `1h`, `4h` and `1d`, each with a duration, UTC epoch
alignment, next-opening calculation and Binance mapping.

## Binance Spot adapter

The adapter uses only the official public market-data host:
`https://data-api.binance.vision`.

- `GET /api/v3/exchangeInfo` lists or resolves instruments;
- `GET /api/v3/klines` returns chronological klines;
- pages never exceed the official 1000-row maximum;
- source strings become `Decimal`, and millisecond epochs become UTC;
- an unchanged page marker or non-advancing cursor is a blocking inconsistency;
- 418 responses are never retried in the same call;
- 429 retries honor `Retry-After` only up to
  `ADT_MARKET_HTTP_MAX_RETRY_AFTER`; larger values are returned to the caller
  without being silently capped;
- timeouts, network failures and 5xx responses use bounded exponential backoff
  with jitter;
- raw payload errors and request URLs are not logged or printed.

Open candles are excluded by default. With
`ADT_MARKET_ALLOW_OPEN_CANDLES=true`, the adapter may return them and dry-run or
diagnostic output may report them with a quality warning. Persistent ingestion
always removes open candles before planning the upsert, and the storage layer
also rejects them defensively. A closed revision for the same `open_time` can
therefore be persisted later without conflict.

Before resolving an instrument or issuing any HTTP request, ingestion computes
the maximum candle count implied by the half-open interval. Intervals above
`ADT_MARKET_MAX_FETCH_CANDLES` fail safely and must be divided; partial network
fetches are not started.

## Dataset and Parquet schema

All paths are rooted at `ADT_DATA_DIR`; symbols are constructed only from
validated canonical asset identifiers:

```text
market/
  exchange=binance/
    market=spot/
      base=BTC/
        quote=USDT/
          timeframe=1h/
            year=2026/
              month=01/
                candles.parquet
  .transactions/
  catalog.json
```

The explicit schema uses:

- UTF-8 strings for identity/source fields;
- `timestamp[ms, tz=UTC]` for both timestamps;
- `decimal128(38, 18)` for every price and volume;
- nullable `int64` trade count and nullable quote volume;
- non-null Boolean `is_closed`.

The separate, reversibly encoded base and quote components prevent identities
such as `A_B/C` and `A/B_C` from colliding. Every constructed and resolved path
must remain below `ADT_DATA_DIR/market`; absolute paths, traversal and existing
symlink components are rejected.

Reads touch only intersecting monthly partitions and verify that every row has
the exchange, market type, symbol and timeframe implied by the call and path.
Existing files must also have strictly increasing `open_time`, unique keys,
exact millisecond timestamps, and rows belonging to the year and month encoded
by their partition path. Corrupt existing duplicates are rejected before the
upsert merge, so dictionary construction cannot conceal them.
Upsert loads and rewrites only the touched month, deduplicates by canonical key
and sorts by `open_time`. Identical duplicates are counted; conflicting content
for the same key is rejected, including conflicts inside one received batch.
Decimals must be exactly representable as `decimal128(38,18)` and timestamps
must have exact millisecond precision before any file is changed.

## Atomic writes, catalog and recovery

Each ingestion has a persistent atomic journal under `.transactions`. A
fsynced `PREPARED` record contains the transaction ID, target partitions,
temporary files, backups, prior catalog backup, and intended version/checksum.
Only then are new Parquet files written and fsynced. Partition and catalog
targets are promoted while their backups remain available. Finally the journal
is atomically marked and fsynced as `COMMITTED`, after which backups and the
journal are removed.

The successful fsync of `COMMITTED` is the result boundary. Any earlier failure
rolls the catalog and all partitions back and returns an ingestion error.
Cleanup failure after that boundary is logged with sanitized metadata, does not
turn the completed run into `FAILED`, and leaves the committed journal and
remaining backups for the next recovery.

Startup recovery is idempotent. `PREPARED` means the catalog and every partition
are rolled back; `COMMITTED` means promoted files are retained and leftover
backups are removed. Runs left `RUNNING` by an interrupted process become
sanitized `FAILED` records. Directory entries are fsynced after catalog and
partition replacements.

Recovery validates the complete journal before touching any referenced path:
state, UUIDs, SHA-256 values, unique targets, catalog identity, artifact
directories/names and exclusion of catalog or `.transactions` as partition
targets. An inconsistent journal is rejected without modifying its artifacts.
Catalog completion accepts only the exact persisted `run_id`, `dataset_key` and
`started_at`, and is available only through the transaction coordinator.

The catalog stores only dataset identity, logical location, bounds, count,
content-state version and sanitized ingestion status. Candles remain in
Parquet. No Phase 2A PostgreSQL migration is necessary; a database catalog is
deferred until multi-process workers or an administrative API require it.

## Quality policy

Blocking errors include duplicates, ordering problems, gaps, misalignment,
invalid OHLC, negative volume, future closed candles, overlaps and values that
exceed Parquet decimal scale. Open candles and an incomplete requested boundary
are warnings. Missing intervals also receive an informational finding stating
that Phase 2A never fills gaps artificially.

## CLI

```bash
python -m app.cli market-data instruments --exchange binance --market spot

python -m app.cli market-data fetch \
  --exchange binance --market spot --symbol BTC/USDT --timeframe 1h \
  --start 2026-01-01T00:00:00Z --end 2026-01-01T06:00:00Z --dry-run

python -m app.cli market-data inspect \
  --exchange binance --market spot --symbol BTC/USDT --timeframe 1h

python -m app.cli market-data verify \
  --exchange binance --market spot --symbol BTC/USDT --timeframe 1h \
  --start 2026-01-01T00:00:00Z --end 2026-01-01T06:00:00Z
```

Exit codes are `0` for success, `2` for CLI argument errors, `3` for safe
domain/quality failures and `4` for unexpected local failures. Output is a
bounded JSON summary.

## Optional manual smoke test

Do not run this as part of the normal suite. To authorize a minimal public
network request explicitly:

```bash
cd services/backend
ADT_ALLOW_NETWORK_TESTS=true .venv/bin/pytest \
  tests/manual/test_market_network_smoke.py -q
```

It uses no credentials and requests at most two one-minute candles. Leave the
environment variable unset for all routine validation.

## Current limitations

- Binance Spot is the only implemented adapter.
- No scheduler, resume planner, distributed lock or permanent worker exists.
- Month upserts are bounded, but a single very large monthly partition is still
  read for merge.
- The local JSON catalog is single-process operational state, not a
  multi-process coordination database.
- No administrator HTTP endpoint or frontend market-data screen exists.
