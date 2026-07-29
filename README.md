# ADT — Automatic Dry Trade

**A disciplined, emotion-free automated trading robot for backtesting and paper trading.**

## Status

🔄 **Phase 0 — Foundation** (Active)

This is the architectural foundation. The system is not yet connected to real exchanges or live markets.

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

- Node.js 18+
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

### 3. Backend

```bash
cd services/backend
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -e ".[dev]"
python -m uvicorn app.main:app --reload
# API runs at http://localhost:8000
```

### 4. Test the Connection

Visit http://localhost:5173. You should see:
- ADT title and status
- API connection status (✓ or ⚠️)
- Component cards

## Project Structure

