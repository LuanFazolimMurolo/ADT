# Deterministic Backtesting (Phase 3A)

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

Sharpe, Sortino, CAGR, comparative reports and statistical analysis remain for
Phase 3B.

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

Phase 3A does not implement indicators, production strategies, optimization,
walk-forward analysis, batch runs, multiple assets, partial fills, order-book
simulation, frontend charts, schedulers, paper trading or live trading.
