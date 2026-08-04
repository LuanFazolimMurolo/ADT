# ADT — Automatic Dry Trade

**A disciplined, emotion-free automated trading robot for backtesting and paper trading.**

## Status

🟡 **Phase 5 paper-trading runtime in progress — 5-01 through 5-08 implemented locally**

⏳ **Formal Phase 1 closure pending operational homologation**

The backend exposes a read-only Binance Spot asset catalog, can maintain an
explicit bounded set of RAW Parquet candle datasets from a separate collector
process, can replay deterministic local paper sessions over those closed
candles, can size simulated Spot entries and enforce fixed-percent protective
stops deterministically, can classify closed candles as trend, range or volatile
without look-ahead, and can aggregate verified backtest results by canonical asset.
It still performs no permanent strategy scheduling, exchange-account
operation or real-capital trading. No migration or administrator bootstrap has
been run against a remote Supabase project as part of this work.

## What is ADT?

ADT is a trading robot designed to:

- **Backtest** historical candle data with configurable strategies
- **Paper trade** with simulated capital (no real money)
- **Learn** strategy performance per asset, timeframe, and market regime
- **Risk-manage** with automated position sizing and stop-loss enforcement
- **Operate** without emotional bias, following strict algorithmic rules
- **Scale** to multiple markets and timeframes via plugin architecture

## Tech Stack

- **Frontend**: React + Vite + TypeScript (Vercel)
- **Backend**: Python + FastAPI (Persistent server)
- **Database**: Supabase (PostgreSQL + Auth)
- **Historical market data**: canonical Decimal/UTC candles in monthly Parquet
- **Paper Trading**: Simulated capital only
- **Deployment**: Docker + Docker Compose

## Quick Start

### Prerequisites

- Node.js 20+
- Python 3.11+
- (Optional) Docker & Docker Compose

### 1. Clone & Setup Environment

```bash
git clone <repo-url> adt
cd adt
cp .env.example .env
# Edit .env if needed (defaults work for local dev)
```

### 2. Frontend

```bash
cd apps/web
npm install
npm run dev
# Open http://localhost:5173
```

The public site has no registration or visible login. Administrative access is
available only at `http://localhost:5173/admin/login`. Configure the three
required public variables documented in
[`apps/web/README.md`](./apps/web/README.md) before starting Vite.

### 3. Backend

```bash
cd services/backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
.venv/bin/python -m app.main
# API runs at http://localhost:8000
```

### 4. Test the Connection

Visit http://localhost:5173. You should see:
- the public ADT presentation;
- API connection status;
- no visible registration or administrative login.

For the full local frontend/backend workflow, private routes, Supabase password
Redirect URLs and quality commands, see
[`apps/web/README.md`](./apps/web/README.md).

### 5. Validate Phase 1 locally

```bash
cd services/backend
.venv/bin/ruff format --check app scripts tests
.venv/bin/ruff check app scripts tests
.venv/bin/mypy app scripts
.venv/bin/pytest

cd ../../apps/web
npm run generate:api
npm run typecheck
npm run typecheck:e2e
npm run lint
npm test -- --run --silent
npm run test:e2e
npm run build
```

The PostgreSQL tests create an isolated temporary cluster. Playwright starts
Vite and intercepts Supabase Auth/FastAPI only on explicit loopback origins;
unexpected or remote network requests fail the test.

The complete release gate, security checklist and manual Supabase homologation
steps are in
[`docs/PHASE1_HOMOLOGATION.md`](./docs/PHASE1_HOMOLOGATION.md).

### 6. Local market data (Phase 2A)

After configuring `ADT_DATA_DIR`, inspect local datasets without network:

```bash
cd services/backend
.venv/bin/python -m app.cli market-data inspect \
  --exchange binance --market spot --symbol BTC/USDT --timeframe 1h
```

Fetching is an explicit network operation. Start with a small dry run:

```bash
.venv/bin/python -m app.cli market-data fetch \
  --exchange binance --market spot --symbol BTC/USDT --timeframe 1h \
  --start 2026-01-01T00:00:00Z --end 2026-01-01T06:00:00Z --dry-run
```

See [Market Data Phase 2A](./docs/MARKET_DATA.md) for the canonical schema,
partition layout, safety limits and recovery procedure.

## Project Structure

```text
apps/web/          React/Vite public and administrative frontend
services/backend/  FastAPI, PostgreSQL services and modular market data
supabase/          ordered, versioned database migrations
docs/              specification, architecture and operational guides
```


### Public market assets (Phase 5-01)

With the backend running, the read-only Binance Spot catalog is available at
`GET /api/v1/market/assets`. Asset metadata and current public prices require no
Binance API key; no account or order endpoint is used.


### Continuous RAW collection (Phase 5-02)

The continuous collector is a process separate from FastAPI. Both processes
must resolve the same persistent `ADT_DATA_DIR`. Targets are explicit and use
`BASE/QUOTE:TIMEFRAME`; the collector never expands the whole exchange catalog
automatically.

Run one bounded cycle:

```bash
cd services/backend
.venv/bin/python -m app.cli market-data collect run-once \
  --target BTC/USDT:1m --bootstrap-candles 1440 --yes
```

Run continuously under a process supervisor:

```bash
.venv/bin/python -m app.cli market-data collect loop \
  --target BTC/USDT:1m --target ETH/USDT:5m \
  --interval-seconds 30 --yes
```

Inspect the latest atomic cycle state without network access:

```bash
.venv/bin/python -m app.cli market-data collect status
```

FastAPI exposes the same read-only state at
`GET /api/v1/market/collection/status`. A cycle is `NOOP` for a target when the
latest closed candle is already stored, so sub-timeframe polling does not
re-fetch candles. No API key, account endpoint or trading permission is used.


### Deterministic paper sessions (Phase 5-03)

Create, advance, inspect and verify local simulated sessions with the
`paper-trading` CLI group. A cycle replays the bounded closed-candle prefix
through the existing Phase 3 engine and publishes an authenticated latest state;
it never sends an exchange order. See
[`docs/PAPER_TRADING.md`](./docs/PAPER_TRADING.md) for commands and limits.

### Continuous paper runner and API (Phase 5-04)

Run explicit sessions continuously outside FastAPI with `paper-trading runner`.
The API exposes only paginated local state at `/api/v1/paper-trading/...`; HTTP
requests never execute strategies. See
[`docs/PAPER_TRADING.md`](./docs/PAPER_TRADING.md) for commands, endpoints and
safety limits.

### Deterministic position sizing (Phase 5-05)

Backtests and paper sessions default to the legacy `explicit_quantity` policy.
Opening Spot buys may instead use `fixed_notional` or `equity_percent` through
`--position-sizing` and `--position-sizing-value`. The engine projects quantity
with `Decimal`, adverse execution assumptions, fees, quantity step and quote
reserve before the existing risk manager performs its final veto. Sales retain
the strategy's explicit quantity.

### Deterministic stop-loss enforcement (Phase 5-06)

Backtests and paper sessions may enable an engine-managed full-position stop with
`--stop-loss fixed_percent --stop-loss-value <percent>`. The compatibility default
remains `disabled`. Enabled protection is persisted in canonical run, session and
experiment identities, maintained after position-changing fills, and executed by
the existing deterministic `STOP_MARKET` model without exchange access.

### Asset-level performance tracking (Phase 5-07)

Between 1 and 100 verified completed backtests can be grouped deterministically by
canonical exchange, market type and symbol. The local `backtest
asset-performance-generate` command produces a content-addressed report without
network access. `asset-performance-export --yes` publishes it atomically under
`market/asset-performance-reports/<report_id>/`; the matching `inspect` and
`verify` commands validate the exact report, manifest and source-run bindings.
Capital, profit, run counts, closed trades and drawdown are consolidated, while
non-additive ratios such as Sharpe, Sortino and profit factor are deliberately not
averaged.

### Deterministic market regime detection (Phase 5-08)

Backtests may opt in with `--market-regime`. The versioned heuristic classifies
each closed candle as `warmup`, `trend`, `range` or `volatile` from fast/slow EMA
and normalized ATR values, with a separate `up`, `down` or `none` trend direction.
The policy is part of the run identity, observations are exposed to strategies only
after their candle closes, and verified runs persist an optional `regimes.jsonl`
artifact that can be paged with `backtest regimes`. Legacy runs remain byte-for-byte
compatible and do not produce regime data.

Paper sessions use the same detector when explicitly enabled. Regime-aware sessions
persist only the latest verified closed-candle observation in their bounded latest
state; legacy schema-1 sessions remain unchanged. Phase 5-08 is deterministic
feature engineering, not the machine-learning regime classifier deferred to Phase 8,
and it does not automatically select or promote a strategy.
