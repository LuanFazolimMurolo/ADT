# ADT Specification

> **Authority note:** This document preserves foundational and historical ADT
> product material, including an obsolete phase ordering. The current durable
> product direction is [`PRODUCT_VISION.md`](./PRODUCT_VISION.md), and the
> current authoritative phase plan is [`ROADMAP.md`](./ROADMAP.md). When phase
> numbering or future scope conflicts, `PRODUCT_VISION.md` and `ROADMAP.md`
> take precedence. See [`CHAT_CONTINUITY.md`](./CHAT_CONTINUITY.md) for the
> current development handoff.

## What is ADT?

**ADT** = **A**utomatic **D**ry **T**rade

A disciplined, emotion-free automated trading robot for backtesting and paper trading.

**"Dry"** symbolizes:
- Cold, calculated decision-making
- No emotional interference with profits/losses
- Mechanical rule-following
- No human panic or greed

## Product Vision

### Goals

1. **Backtest** historical candle data efficiently
2. **Simulate** trading with fake capital (paper trading)
3. **Learn** strategy performance across assets, timeframes, and market regimes
4. **Manage risk** with automatic position sizing and stops
5. **Scale** to multiple markets via plugin architecture
6. **Deploy** to a persistent server

### Non-Goals

- ❌ Real money trading (Phase 0-5: Paper trading only)
- ❌ Mobile app (Phase 0: Web only)
- ❌ Complex UI/UX (Phase 0: Functional, not beautiful)
- ❌ Machine learning (Phase 8+)
- ❌ Proprietary indicators (Use public ones)

## Users

### Public User (No Account)

- Visits website
- Sees:
  - System status
  - Component overview
  - Documentation links
- Cannot access admin features
- Cannot modify data

### Administrator (Single Account)

- Full system access
- Can:
  - Configure strategies
  - Run backtests
  - Monitor paper trading
  - View results
  - Manage settings
- Authentication via Supabase (Phase 1+)

## Capital Model

### Paper Trading Only

- Simulated capital (e.g., $100,000 fictional USD)
- No real money risked
- Realistic slippage, spread, and fee simulation (Phase 5+)
- Historical backtesting uses real price data

### Position Sizing

- Risk fixed % per trade (e.g., 2% of capital)
- Automatic stop-loss enforcement
- No overtime leverage

## Features by Phase

### Phase 0: Foundation ✅ COMPLETE

- API structure
- Basic UI
- Logging & config
- Test setup

### Phase 1: Supabase & Admin

- PostgreSQL schema
- User authentication (single admin)
- Admin dashboard skeleton
- Basic CRUD operations

### Phase 2: Market Data Collection 🚧 CURRENT

- Historical candle fetching through a public adapter
- Parquet storage
- Multi-symbol, multi-timeframe support
- Data validation
- Deterministic derived datasets and immutable local snapshots
- Approved authenticated operational administration and durable single-host
  worker (Phase 2D)

### Phase 3: Strategies & Indicators

- Strategy plugin architecture
- Indicator library (RSI, EMA, MACD, etc.)
- Configurable parameters
- Rule engine

### Phase 4: Backtesting Engine

- Realistic candle-by-candle simulation
- Look-ahead bias prevention
- Fee & slippage application
- Performance metrics (Sharpe, Sortino, etc.)

### Phase 5: Risk Management & Paper Trading

- Live position size calculation
- Stop-loss enforcement
- Asset-level learning
- Regime detection

### Phase 6: Telegram Integration

- Trade notifications
- Daily summaries
- Error alerts
- Admin commands

### Phase 7: Admin Dashboard

- Strategy performance charts
- Backtest results visualization
- Position monitoring
- Trade history

### Phase 8: Machine Learning

- Strategy optimization
- Parameter tuning
- Regime prediction

### Phase 9: Production Deployment

- Docker containers
- Cloud hosting
- Persistent worker
- Health monitoring

### Phase 10: Validation & Iteration

- Live paper trading (extended)
- Performance analysis
- Refinements

## Technology Stack

| Component | Technology | Version | Notes |
|-----------|-----------|---------|-------|
| Frontend | React | 18+ | UI layer |
| Frontend Build | Vite | 5+ | Fast dev experience |
| Frontend Language | TypeScript | 5+ | Type safety |
| Frontend Styling | CSS | Modern | No framework Phase 0 |
| Frontend Deploy | Vercel | Latest | Auto-deploy on push |
| Backend | Python | 3.11+ | Math & async |
| Backend Framework | FastAPI | 0.104+ | Type hints, async |
| Backend Language | Python | 3.11+ | Type hints required |
| Backend Deploy | Python Server | - | Persistent, custom |
| Database | PostgreSQL | 15+ | Via Supabase |
| Database ORM | TBD | - | Chosen in Phase 1 |
| Authentication | Supabase Auth | Latest | JWT-based |
| Authorization | RLS | - | Row-level security |
| Containerization | Docker | Latest | Dev & prod |
| Orchestration | Docker Compose | Latest | Local dev |
| Message Queue | TBD | - | Phase 6+ |
| Caching | TBD | - | Phase 5+ |

## Data Model (Sketch)

### Core Tables (Phase 1)

```sql
users
├── id PK
├── admin boolean
└── created_at

strategies
├── id PK
├── name
├── rules
└── admin_id FK

backtests
├── id PK
├── strategy_id FK
├── start_date, end_date
├── params
├── result_metrics
└── created_at

trades
├── id PK
├── backtest_id FK
├── symbol
├── side (BUY/SELL)
├── quantity, price
├── fees
├── pnl
└── timestamp
```

## API Surface (Phase 1+)
