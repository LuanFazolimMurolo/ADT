# ADT Market Data — Phases 2A and 2B

## Scope and safety

Phase 2A represents, fetches, validates and stores bounded historical candles.
It supports Binance Spot public market data without credentials. It does not
implement strategies, backtests, orders, paper-trading execution, scheduled
workers or large backfills.

Phase 2B composes those bounded transactions into resumable historical
backfills, incremental updates and explicit gap repairs. It remains a local,
operator-driven facility: there is no scheduler, daemon, HTTP administration
route or frontend flow. Automated tests never access the network.

## Phase 2B planning and jobs

Plans use half-open UTC ranges and integer `timedelta` division. A chunk is
bounded by the smallest of the adapter request limit,
`ADT_MARKET_MAX_FETCH_CANDLES` and `ADT_MARKET_BACKFILL_CHUNK_CANDLES`. The
entire plan is additionally bounded by
`ADT_MARKET_BACKFILL_MAX_TOTAL_CANDLES` and `ADT_MARKET_JOB_MAX_CHUNKS`.
Misaligned or partially covered ranges are rejected before HTTP access.

Execution is deliberately sequential. Each chunk goes through the complete
Phase 2A journaled Parquet/catalog transaction, and only then advances the
separate atomic `market/jobs.json` checkpoint. A failed or paused job resumes
at its first unconfirmed chunk. The persisted plan identity, ranges and
checksum are immutable. Status and logs expose only bounded counts, IDs,
dataset identity, chunk index, durations and sanitized error codes.

Every persistent ingestion, including the single-range `fetch`, owns an
explicit dataset lease. The executor retains one lease for the complete job and
passes it into each chunk ingestion; the service validates that it is active
and matches the exact exchange/market/symbol/timeframe key. A caller without a
lease acquires one before source metadata lookup or other persistent work.
Pending journals for that exact dataset are recovered before any read, fetch
or run creation. Dry-runs do not acquire an exclusive write lease.

An advisory file lock under `market/.locks` is derived from the complete
dataset key. Concurrent jobs for the same dataset fail after
`ADT_MARKET_JOB_LOCK_TIMEOUT`; different datasets use different locks. Lock
files are retained and reused, so deleting stale-looking files is never used
as a substitute for the kernel lock. Abandoned `RUNNING` checkpoints may be
marked `FAILED` with the sanitized `interrupted_job` code during controlled
startup recovery and then resumed explicitly. CLI job commands perform this
recovery automatically. `ADT_MARKET_JOB_STALE_AFTER` describes lock metadata
age only; it never overrides a live kernel `flock`.

The main `catalog.json` has a separate global `.catalog.lock`. Read-modify-write
operations are serialized, and completion retains that lease from the fresh
catalog read and `prepare_completion` through the journal's durable
`COMMITTED`. Catalog reads also take this lease, and promotion creates a durable
hard-link backup before atomically replacing `catalog.json`, so readers observe
one complete version. Recovery always follows dataset lock, catalog lock, then
files; it never takes a dataset lock while holding the catalog lock.

Each job chunk also commits an immutable receipt inside `catalog.json` in the
same transaction as its Parquet and dataset metadata. It records the job and
chunk identity, exact interval, original fetched/stored/duplicate/request
counts, resulting version/checksum and commit timestamp. If a process stops
after this commit but before `jobs.json` advances, resume validates the exact
Parquet interval and receipt, restores the original metrics, and does not call
the adapter.

Incremental update starts after the newest local candle while refetching
`ADT_MARKET_INCREMENTAL_OVERLAP_CANDLES` already-stored candles. Identical
overlap is idempotent and conflicting source revisions remain blocking errors.
An absent dataset requires an explicit start. If no closed candle is pending,
the planner returns `NOOP`.

Gap discovery compares the expected aligned openings with local storage,
groups consecutive missing candles and never writes. Repair is a distinct
`GAP_REPAIR` job and verifies the requested logical interval after ingestion;
a gap that remains is an error. Neither operation invents candles.
When no gap exists, `GapRepairPlan.backfill` is `None`; no zero-chunk job can
enter `RUNNING`.

Dataset `version` is a SHA-256 of the complete canonical logical candle
content. It is stable across retries, duplicate no-ops, process restarts,
partition boundaries and different valid chunk orders.

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
fsynced `PREPARED` record contains the transaction and dataset IDs, target
partitions, temporary files, backups, intended version/checksum, and the exact
previous/intended dataset, run and optional receipt values.
Only then are new Parquet files written and fsynced. Partition and catalog
targets are promoted while their backups remain available. Finally the journal
is atomically marked and fsynced as `COMMITTED`, after which backups and the
journal are removed.

The successful fsync of `COMMITTED` is the result boundary. Any earlier failure
rolls the catalog and all partitions back and returns an ingestion error.
Cleanup failure after that boundary is logged with sanitized metadata, does not
turn the completed run into `FAILED`, and leaves the committed journal and
remaining backups for the next recovery.

Startup recovery is idempotent. `PREPARED` semantically reverts only the owned
dataset/run/receipt keys and rolls back its partitions; commits belonging to
other datasets remain intact. A current owned value that matches neither the
previous nor intended journal value is an inconsistency and is never
overwritten. `COMMITTED` retains promoted files and removes leftover backups.
Runs left `RUNNING` by an interrupted process become sanitized `FAILED`
records. Directory entries are fsynced after catalog and partition
replacements.

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

python -m app.cli market-data backfill plan \
  --symbol BTC/USDT --timeframe 1h \
  --start 2025-01-01T00:00:00Z --end 2025-02-01T00:00:00Z

python -m app.cli market-data backfill run \
  --symbol BTC/USDT --timeframe 1h \
  --start 2025-01-01T00:00:00Z --end 2025-02-01T00:00:00Z --yes

python -m app.cli market-data backfill status --job-id JOB_UUID
python -m app.cli market-data backfill pause --job-id JOB_UUID
python -m app.cli market-data backfill cancel --job-id JOB_UUID
python -m app.cli market-data backfill resume --job-id JOB_UUID --symbol BTC/USDT
python -m app.cli market-data update --symbol BTC/USDT --timeframe 1h
python -m app.cli market-data gaps --symbol BTC/USDT --timeframe 1h \
  --start 2025-01-01T00:00:00Z --end 2025-02-01T00:00:00Z
python -m app.cli market-data repair --symbol BTC/USDT --timeframe 1h \
  --start 2025-01-01T00:00:00Z --end 2025-02-01T00:00:00Z --yes
```

Exit codes are `0` for success, `2` for CLI argument errors, `3` for safe
domain/quality failures and `4` for unexpected local failures. Output is a
bounded JSON summary. Multi-chunk writes require `--yes`; plan, status, gaps
and `--dry-run` do not write market data.

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
- No scheduler, distributed lock or permanent worker exists.
- Month upserts are bounded, but a single very large monthly partition is still
  read for merge.
- The local job catalog and file locks coordinate one host, not distributed
  workers or shared filesystems with uncertain advisory-lock semantics.
- No administrator HTTP endpoint or frontend market-data screen exists.
