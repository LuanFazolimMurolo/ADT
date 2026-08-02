# ADT — Automatic Dry Trade

**A disciplined, emotion-free automated trading robot for backtesting and paper trading.**

## Status

🟡 **Phase 2A market-data foundation implemented locally**

⏳ **Formal Phase 1 closure pending operational homologation**

The backend now has an opt-in public Binance Spot adapter and local Parquet
datasets. It does not trade, run strategies or contact an exchange unless an
operator explicitly invokes a network command. No migration or administrator
bootstrap has been run against a remote Supabase project as part of this work.

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
