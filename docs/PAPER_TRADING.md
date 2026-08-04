# Deterministic Paper Trading

Phase 5-03 adds the first local paper-trading execution boundary. It consumes
only closed Binance Spot RAW candles already committed by the Phase 2/5-02
market-data pipeline. It does not contact Binance, access an account or submit
an exchange order.

## Model

A paper session is one immutable identity containing:

- canonical pair and timeframe;
- evaluation start and optional lifecycle-2 warmup;
- versioned strategy descriptor and normalized parameters;
- initial simulated capital;
- fee, slippage, position-sizing, instrument and risk assumptions;
- bounded replay, order and event limits;
- engine and document schema versions.

The domain-separated session ID is derived from this complete configuration.
Changing any trading assumption creates a different session instead of mutating
an existing one.

Phase 5-03 deliberately replays the complete bounded session range on every
`run-once`. The source range and candles are verified before the existing
Phase 3 engine is invoked. This makes the latest state independently
reproducible without persisting opaque Python strategy state. Incremental
strategy checkpoints and permanent scheduling remain deferred.

## Order behavior at a cycle boundary

The ordinary backtest API continues to cancel open orders at the end of a run.
Paper replay calls the same engine with terminal cancellation disabled. An
order emitted on the latest available candle therefore remains `OPEN`; after a
new closed candle arrives, the next complete replay can fill or expire it under
the same deterministic rules. `force_close_at_end` is forbidden for paper
sessions.

No state is carried from one engine instance to another. Portfolio, strategy,
indicators, ledger, orders and risk state are reconstructed from the frozen RAW
prefix on every cycle.

## Storage

Files are stored below:

```text
$ADT_DATA_DIR/market/paper-trading/<session_id>/
  config.json
  state.json
```

Both documents are strict canonical JSON. The configuration has a checksum and
session ID. The latest state records the dataset version, source-range checksum,
orders, fills, portfolio, risk halt, state ID and checksum. Publication holds a
session lock, writes and fsyncs a temporary file, atomically replaces the target,
fsyncs the directory and decodes the result again. Config and state documents
are limited to 16 MiB; oversized input and duplicate JSON keys are rejected.

A later state may extend an earlier range, but publication rejects regression or
another result for the same range. `verify` reloads the exact persisted candle
range, authenticates its logical checksum and reproduces the complete state.

## Commands

Create a lifecycle-1 no-op session:

```bash
cd services/backend
.venv/bin/python -m app.cli paper-trading create \
  --symbol BTC/USDT \
  --timeframe 1m \
  --start 2026-08-02T20:00:00Z \
  --strategy no-op \
  --strategy-version 1 \
  --initial-capital 10000 \
  --minimum-quantity 0.00001 \
  --quantity-step 0.00001 \
  --price-tick 0.01 \
  --minimum-notional 5 \
  --yes
```

The command returns the deterministic `session_id`. Execute one replay after
5-02 has committed new closed candles:

```bash
.venv/bin/python -m app.cli paper-trading run-once \
  --session-id <session_id> \
  --yes
```

Read the latest local state without executing the strategy:

```bash
.venv/bin/python -m app.cli paper-trading status \
  --session-id <session_id>
```

Reproduce and verify it:

```bash
.venv/bin/python -m app.cli paper-trading verify \
  --session-id <session_id>
```

Decimal strategy parameters in `--parameters-json` must be JSON strings. For
example:

```text
--parameters-json '{"fast_period":10,"slow_period":30,"quantity":"0.01"}'
```

## Position sizing (Phase 5-05)

Paper-session creation accepts the same deterministic sizing policies as a
backtest:

```text
--position-sizing explicit_quantity
--position-sizing fixed_notional --position-sizing-value 250
--position-sizing equity_percent --position-sizing-value 10
```

`explicit_quantity` is the default and preserves legacy session identities. For
opening buys, the other policies ignore the strategy-provided quantity and derive
a quantity from quote notional or current simulated equity. Estimated slippage,
fees, quantity step and quote reserve are applied before the existing risk
manager. Sales continue to use explicit strategy quantity. The selected policy is
persisted in canonical session and experiment documents and changes their
deterministic identity. Existing documents without the extension decode as
`explicit_quantity`.

Position sizing does not add exchange access or live orders.

## Stop-loss enforcement (Phase 5-06)

Paper-session creation accepts the same compatibility-preserving policy as a
backtest:

```text
--stop-loss disabled
--stop-loss fixed_percent --stop-loss-value 5
```

`disabled` is omitted from the canonical risk payload, so existing session IDs
and documents remain unchanged. An enabled fixed-percent policy is persisted in
the session identity and round-trips through local paper documents and experiment
manifests. The replay engine maintains one full-position `STOP_MARKET` order after
position-changing fills, replaces it when weighted average entry price or quantity
changes, and removes it when the position becomes flat. Gap, touch, slippage, fee,
order-limit and drawdown-halt behavior is identical to deterministic backtesting.
No exchange account, API key or live order is involved.

## Safety limits

`ADT_PAPER_TRADING_MAX_REPLAY_CANDLES` limits the complete context loaded by one
cycle. Its default is `200000`, it may not exceed `ADT_BACKTEST_MAX_CANDLES`, and
all existing backtest order, open-order, event, history, fee and slippage limits
remain active. The local source additionally requires:

- exact pair/timeframe identity;
- Binance Spot closed candles only;
- strict contiguous open times;
- exact close times;
- complete context and evaluation coverage;
- canonical candle serialization;
- a matching logical source checksum.

## Explicitly deferred

Phase 5-03 does not add:

- a permanent paper-session scheduler;
- automatic strategy selection or production promotion;
- an HTTP mutation API or dashboard;
- PostgreSQL/Supabase session persistence;
- cross-process event streaming;
- incremental serialization of arbitrary strategy state;
- exchange account access, API keys or live orders;
- real-capital trading.


## Continuous runner (Phase 5-04)

Execute one cycle for explicit sessions:

```bash
.venv/bin/python -m app.cli paper-trading runner run-once \
  --session-id <session_id> \
  --session-id <another_session_id> \
  --yes
```

Run at a fixed cadence:

```bash
.venv/bin/python -m app.cli paper-trading runner loop \
  --session-id <session_id> \
  --interval-seconds 30 \
  --yes
```

For bounded operational validation, `--max-cycles N` stops the loop after N
cycles. Read the latest runner state without executing a session:

```bash
.venv/bin/python -m app.cli paper-trading runner status
```

Only one runner may hold the volume-wide lease for an `ADT_DATA_DIR`. Sessions
are unique, sorted and limited by
`ADT_PAPER_TRADING_CONTINUOUS_MAX_SESSIONS`; cadence defaults to
`ADT_PAPER_TRADING_CONTINUOUS_INTERVAL_SECONDS`. One session failure does not
prevent later sessions in the same cycle.

### Read-only API

```text
GET /api/v1/paper-trading/runner/status
GET /api/v1/paper-trading/sessions
GET /api/v1/paper-trading/sessions/{session_id}
GET /api/v1/paper-trading/sessions/{session_id}/orders
GET /api/v1/paper-trading/sessions/{session_id}/fills
```

The endpoints only decode and validate local canonical documents. They never
create a session, advance a replay or contact Binance. Orders and fills are
paginated, and every financial value is serialized as a JSON decimal string.

Phase 5-04 still does not add dynamic subscriptions, mutation endpoints, a
dashboard, PostgreSQL persistence, WebSocket streaming, notifications, strategy
promotion or live orders.
