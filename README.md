# ADT — Automatic Dry Trade

**A disciplined, emotion-free automated trading robot for backtesting and paper trading.**

## Status

🟡 **Phase 1D implemented; candidate gate and operational homologation pending**

⏳ **Formal Phase 1 closure pending operational homologation**

This is the architectural foundation. The system is not yet connected to real exchanges or live markets.
No migration or administrator bootstrap has been run against a remote Supabase
project as part of this implementation.

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

## Project Structure

```text
apps/web/          React/Vite public and administrative frontend
services/backend/  FastAPI, JWT verification and PostgreSQL services
supabase/          ordered, versioned database migrations
docs/              specification, architecture and operational guides
```
