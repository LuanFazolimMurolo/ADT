# Deterministic Backtesting (Phases 3A–3B and Phase 4-01 input contracts)

Phase 3A implements a local, reproducible, candle-by-candle backtest engine for
one immutable Phase 2C snapshot. It is a technical simulation facility, not a
financial recommendation and not an order-routing system.

## Scope

The implemented boundary is intentionally narrow:

- one snapshot, instrument and timeframe per run;
- crypto Spot only;
- long-only, no leverage, margin, shorts or derivatives;
- quote-asset initial capital and base-asset position;
- `Decimal` for all financial calculations;
- no external network access during planning, execution or verification;
- no arbitrary Python module loading from the CLI.

`NoOpStrategy` and `BuyAndHoldExample` are technical examples. The latter is
not a trading recommendation. `ScriptedStrategy` exists only for deterministic
tests and is not registered in the operational CLI.

## Immutable input

The engine consumes only a Phase 2C snapshot. At open time it validates the
snapshot metadata, copied dataset manifest and partition checksums. After the
last candle it calls `verify_unchanged()` again. A changed snapshot aborts the
run and no `COMPLETE` result is published.

Intervals are half-open: `[start, end)`. When the CLI omits `--start` and
`--end`, the complete snapshot range is used.

## Candle cycle and future-leakage prevention

For every closed candle `T`, the engine performs this order:

1. evaluate orders that became eligible before `T`;
2. generate deterministic fills from the current OHLC values;
3. apply fills to portfolio and ledger;
4. mark the portfolio using the close of `T`;
5. append `T` to the bounded strategy history;
6. call `on_candle` with only processed history;
7. validate returned intents and create orders eligible from `T+1`.

An order created by `on_candle` at `T` cannot fill at `T`. A market order fills
at the open of its first eligible candle. Strategies never receive the reader,
filesystem paths, future iterators or mutable engine state.

## Orders and fills

Phase 3A supports:

- sides: `BUY`, `SELL`;
- types: `MARKET`, `LIMIT`, `STOP_MARKET`;
- time in force: `GTC`, `IOC`, `DAY`;
- all-or-none fills only.

Priority is deterministic: eligible candle, creation sequence, then order ID.
OHLC data cannot reveal the real intrabar path. The manifest therefore records
a conservative intrabar assumption and the engine never invents tick data.

Market and stop-market executions use adverse fixed-basis-point slippage and
taker fees. Limit fills never violate the submitted limit and use the configured
maker fee assumption. Fees are charged in the quote asset.

## Portfolio, risk and ledger

The Spot portfolio tracks quote cash, base quantity, average entry price, cost
basis, realized and unrealized PnL, fees, slippage cost, equity, peak equity and
drawdown. Cash and position can never become negative. Sales use average-cost
accounting and may close only available quantity.

Risk validation covers instrument precision, minimum quantity/notional, order
and position notionals, open/total order limits, quote reserve and maximum
drawdown. A configured drawdown halt cancels open orders, blocks new orders and
continues mark-to-market until the interval ends.

The local backtest ledger is append-only and separate from the Supabase paper
simulation ledger. Every entry contains a sequence and chained SHA-256 hash.
Verification detects modification, deletion, reordering, duplication or broken
balances.

## Metrics

Phase 3A derives deterministic return, PnL, fee, slippage, drawdown, order, fill,
closed-trade, win-rate, profit-factor, expectancy, exposure, turnover and
buy-and-hold comparison metrics. Undefined divisions are represented as `null`,
not infinity.

Phase 3B adds time-normalized metrics from the immutable equity curve:

- `return_periods` and exact `elapsed_seconds`;
- observed `periods_per_year`, derived from actual UTC timestamps;
- CAGR using a fixed 365-day crypto year;
- annualized population volatility and downside deviation;
- Sharpe and Sortino ratios using a zero risk-free rate.

The first production return period starts at the configured half-open backtest
range start and ends at the first closed candle. Later periods are measured
between consecutive candle close timestamps. Observation timestamps must be
strictly increasing. A zero denominator is represented as `null`, never
infinity. Decimal calculations use an explicit high-precision local context.

Schema version 1 retains the exact Phase 3A metric payload and checksum.
Schema version 2 includes the advanced Phase 3B fields. This allows the current
verifier to validate previously published Phase 3A artifacts without rewriting
or mutating them.

The second Phase 3B delivery adds a versioned comparison report contract. It:

- accepts between 2 and 100 unique run IDs;
- verifies every immutable run before reading its summary;
- projects only bounded, visualization-safe identity and metric fields;
- supports deterministic ordering by return, CAGR, Sharpe, Sortino, drawdown,
  net profit or profit factor;
- keeps undefined advanced metrics last and uses run ID as a stable tie-breaker;
- exposes whether all entries share the same snapshot, data range and initial
  capital, instead of silently implying an apples-to-apples comparison;
- reads schema version 1 runs with unavailable advanced metrics represented as
  `null`.

The local command is:

```bash
python -m app.cli backtest compare \
  --run-id <RUN_A> \
  --run-id <RUN_B> \
  --sort-by sharpe_ratio
```

Use `--ascending` when lower values should appear first. The command performs no
network request and does not create or mutate artifacts.

The third Phase 3B delivery publishes a portable, content-addressed comparison
export only after every source run passes full verification:

```bash
python -m app.cli backtest compare-export \
  --run-id <RUN_A> \
  --run-id <RUN_B> \
  --sort-by sharpe_ratio \
  --yes
```

Exports are written atomically under
`ADT_DATA_DIR/market/backtest-reports/<report_id>/` and contain exactly
`manifest.json`, `report.json` and `report.csv`. The report ID is derived from
the complete canonical comparison report, so repeated exports are idempotent.
The manifest binds ordered run IDs, logical result checksums and file checksums.
The CSV uses a fixed column order and represents undefined metrics as empty
cells. Verify an export independently with:

```bash
python -m app.cli backtest compare-verify --report-id <REPORT_ID>
```

The fourth Phase 3B delivery adds two read-only, network-free contracts.

A verified equity curve can be projected to at most 2,000 uniformly sampled
points for a chart or administrative preview:

```bash
python -m app.cli backtest visualize \
  --run-id <RUN_ID> \
  --max-points 500
```

The contract preserves the first and last observations, returns only timestamp,
close, equity and drawdown percentage, and includes the verified logical-result
checksum. The complete run is verified before Parquet is read. The source
artifact remains unchanged and no chart file is written.

Multiple explicit comparisons can be evaluated in one bounded request without
creating a parameter grid or selecting a strategy automatically. The JSON file
must contain between 1 and 20 named groups, at most 500 run references and at
most 100 unique runs:

```json
{
  "contract_version": 1,
  "groups": [
    {
      "name": "baseline",
      "run_ids": ["<RUN_A>", "<RUN_B>"],
      "sort_by": "sharpe_ratio",
      "descending": true
    }
  ]
}
```

Run it with:

```bash
python -m app.cli backtest compare-batch --request-file ./comparison-batch.json
```

Every unique run is verified exactly once and reused across groups. The response
has a deterministic `batch_id` bound to all ordered comparison reports. Batch
comparison is read-only: it does not execute backtests, search parameter spaces,
rank a strategy for deployment or publish artifacts.

## Result artifacts

A complete run is published atomically under:

```text
ADT_DATA_DIR/
  market/
    backtests/
      <run_id>/
        manifest.json
        config.json
        result.json
        orders.jsonl
        fills.jsonl
        ledger.jsonl
        equity.parquet
        trades.jsonl
```

The deterministic `run_id` includes the snapshot identity, strategy descriptor,
canonical parameters, capital, interval, execution assumptions, risk limits,
engine version and schema version. Operational timestamps are excluded from the
logical identity.

Publication writes a staging directory, fsyncs artifacts, writes the manifest
last and atomically renames the directory. Existing valid results are reused;
corrupt or logically conflicting results are rejected and never overwritten.

## CLI

Planning performs no writes:

```bash
.venv/bin/python -m app.cli backtest plan \
  --snapshot-id <snapshot-id> \
  --strategy no-op \
  --initial-capital 10000
```

The example strategy requires an explicit base quantity:

```bash
.venv/bin/python -m app.cli backtest run \
  --snapshot-id <snapshot-id> \
  --strategy buy-and-hold-example \
  --quantity 0.01 \
  --initial-capital 10000 \
  --yes
```

A dry run executes the engine but writes no result directory:

```bash
.venv/bin/python -m app.cli backtest run \
  --snapshot-id <snapshot-id> \
  --strategy no-op \
  --initial-capital 10000 \
  --dry-run
```

Inspect and independently verify a result:

```bash
.venv/bin/python -m app.cli backtest inspect --run-id <run-id>
.venv/bin/python -m app.cli backtest verify --run-id <run-id>
.venv/bin/python -m app.cli backtest orders --run-id <run-id> --limit 20
.venv/bin/python -m app.cli backtest trades --run-id <run-id> --limit 20
```

The CLI uses a fixed strategy registry and never imports a module supplied by a
user. Output is bounded JSON. Backtest commands are routed before market HTTP
clients are constructed.

## Deliberate limitations

Phase 4-01 adds only deterministic, finite parameter-search input contracts. It
does not call `DeterministicBacktestEngine`, read snapshots or publish result
artifacts. Every expanded combination contains a deterministic index, complete
normalized strategy parameters, the existing typed Phase 3C parameter document,
its SHA-256 checksum and a combination ID bound to the search-space ID.

The search-space schema is version 1. Its canonical JSON records the exact
plugin identity and schema/lifecycle versions, fixed and searchable parameters,
typed values, strict combination policy, cardinality and requested limit.
`Decimal` is encoded as canonical base-10 text without using the process-global
Decimal context; no `float` is introduced. Canonical output length is checked
before any zero padding, so an extreme exponent cannot trigger proportional
allocation. Canonical integers contain at most 128 magnitude digits, enforced
with exact integer bounds before string conversion. A SHA-256 checksum covers
the payload, while a domain-separated SHA-256 produces the deterministic
search-space ID.

The default expansion limit is 1,000 and the absolute ceiling is 100,000.
Cardinality is rejected before any combination or strategy instance is
materialized. Under `REJECT_SPACE`, one factory-invalid combination rejects the
entire space. Public frozen contracts enforce their invariants even when built
directly, and `expand()` independently rechecks schema, limits, exact
cardinality, checksum and ID before any factory call.

Batch backtest execution, temporal segmentation, experiment manifests,
walk-forward analysis, optimization reports, multiple assets, partial fills,
order-book simulation, frontend chart rendering, schedulers, paper trading and
live trading remain outside Phase 4-01.
