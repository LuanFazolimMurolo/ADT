# ADT Roadmap

Project phases from foundation to full deployment.

## Phase 0: Foundation ✅ COMPLETE

**Goal**: Establish project structure, testing framework, and basic connectivity.

**Deliverables**:
- [x] Project structure (frontend, backend, docs)
- [x] React + Vite + TypeScript frontend
- [x] FastAPI + Python backend
- [x] Environment configuration
- [x] Basic API endpoints (/health, /system/status)
- [x] Logging setup
- [x] Test scaffolding (pytest, vitest)
- [x] Linting & type checking (ruff, mypy, eslint)
- [x] Docker setup
- [x] Documentation (spec, architecture)

**Status**: Complete
**Estimated Duration**: 1 week  
**Blockers**: None

---

## Phase 1: Supabase & Administration 🚧 CURRENT

**Goal**: Add database, user authentication, and admin dashboard skeleton.

**Status**: In progress — the Phase 1A foundation is implemented locally and
Phase 1B is in implementation; Phase 1 as a whole is not complete.

**Deliverables**:
- [ ] Supabase project setup
- [ ] PostgreSQL schema (strategies, backtests, trades, users)
- [x] Database migrations
- [x] JWT authentication (Supabase Auth)
- [x] Row-level security (RLS) policies
- [ ] Admin login page (frontend)
- [ ] Admin dashboard skeleton
- [ ] CRUD endpoints for strategies
- [x] Database-backed administrator authorization middleware

### Phase 1A: Initial Supabase and database structure 🚧

**Scope**: Establish the single versioned migration source, the initial
simulation ledger schema and RLS policies, and the controlled bootstrap for the
first administrator.

**Deliverables**:
- [x] Project-local Supabase CLI structure in `supabase/`
- [x] Initial versioned schema and public active-simulation summary
- [x] RLS policies, constraints, indexes, and audit foundations
- [x] Idempotent initial-administrator bootstrap script and unit tests
- [x] Local/remote setup and security documentation
- [ ] Manually link the reviewed remote project and apply the migration

**Status**: Complete locally; remote application remains a manual operational step.

**Explicitly deferred**: Admin login/dashboard, strategy CRUD, market data,
backtesting, Telegram, machine learning, and completion of Phase 1.

**Dependencies**: Phase 0 complete  
**Estimated Duration**: 2 weeks  
**Blockers**: Supabase account setup

### Phase 1B: Backend persistence and administrative authentication 🚧

**Scope**: Connect FastAPI to the Phase 1A schema, verify Supabase access tokens
with public signing keys, authorize administrators through `app_admins`, and
expose the public and administrative simulation APIs.

**Deliverables**:
- [x] Typed backend configuration with sanitized startup failures
- [x] Asynchronous PostgreSQL pool, explicit transactions and database health
- [x] Supabase JWT verification through cached asymmetric JWKS
- [x] Database-backed administrator dependency
- [x] Public active-simulation endpoint
- [x] Administrative simulation, movement and settings endpoints
- [x] Explicit schemas and safe error contract
- [x] Unit and temporary-PostgreSQL integration tests
- [ ] Configure real project values and validate against Supabase Auth
- [ ] Apply the reviewed migration to the selected remote project

**Status**: Complete locally; remote configuration and application remain
manual pending operations.

**Explicitly deferred**: Frontend login/dashboard, strategy CRUD, market data,
backtesting, Telegram, machine learning, and any real-capital operation.

**Dependencies**: Reviewed Phase 1A migration and an asymmetric Supabase JWT
signing key for deployment.

---

## Phase 2: Market Data Collection

**Goal**: Fetch and store historical candlestick data.

**Deliverables**:
- [ ] Market data adapter interface
- [ ] Binance adapter (OHLCV data)
- [ ] Data models (Candle, OHLCV)
- [ ] Parquet storage (efficient, queryable)
- [ ] Multi-symbol support
- [ ] Multi-timeframe support (1m, 5m, 1h, 1d)
- [ ] Historical data sync job
- [ ] Data validation & cleanup
- [ ] Admin interface to trigger sync

**Dependencies**: Phase 1 complete  
**Estimated Duration**: 2 weeks  
**Blockers**: Market data API access

---

## Phase 3: Strategies & Indicators

**Goal**: Build strategy plugin system and indicator library.

**Deliverables**:
- [ ] Strategy base class & interface
- [ ] Strategy registry (pluggable)
- [ ] Indicator library (RSI, EMA, MACD, Bollinger Bands, ATR, etc.)
- [ ] Rule engine (if/then logic)
- [ ] Parameter configuration (UI + backend)
- [ ] Strategy CRUD endpoints
- [ ] Indicator documentation
- [ ] Example strategies (SMA crossover, RSI overbought, etc.)

**Dependencies**: Phase 2 complete  
**Estimated Duration**: 3 weeks  
**Blockers**: None

---

## Phase 4: Backtesting Engine

**Goal**: Simulate trading on historical data.

**Deliverables**:
- [ ] Backtest engine (candle-by-candle simulation)
- [ ] Look-ahead bias prevention
- [ ] Fee & slippage modeling
- [ ] Performance metrics (Sharpe, Sortino, max drawdown, etc.)
- [ ] Trade journal (entry, exit, PnL)
- [ ] Backtest results storage
- [ ] Results visualization (frontend charts)
- [ ] Batch backtest (multiple strategies/symbols)
- [ ] Backtest worker (async execution)

**Dependencies**: Phase 3 complete  
**Estimated Duration**: 4 weeks  
**Blockers**: None

---

## Phase 5: Risk Management & Paper Trading

**Goal**: Simulate live trading with realistic constraints.

**Deliverables**:
- [ ] Position sizing engine
- [ ] Stop-loss enforcement
- [ ] Asset-level performance tracking
- [ ] Market regime detection (trend/range/volatile)
- [ ] Paper trading engine (real-time simulation)
- [ ] Live performance dashboard
- [ ] Trade history & journal
- [ ] Performance metrics (daily, weekly, monthly)
- [ ] Risk limits & veto system

**Dependencies**: Phase 4 complete  
**Estimated Duration**: 3 weeks  
**Blockers**: None

---

## Phase 6: Telegram Integration

**Goal**: Send notifications and control via Telegram bot.

**Deliverables**:
- [ ] Telegram bot setup
- [ ] Trade notifications
- [ ] Daily summary report
- [ ] Error/alert messages
- [ ] Admin commands (/status, /performance, etc.)
- [ ] Configuration via Telegram
- [ ] Secure token handling

**Dependencies**: Phase 5 complete  
**Estimated Duration**: 1 week  
**Blockers**: Telegram bot token

---

## Phase 7: Admin Dashboard (Advanced)

**Goal**: Comprehensive monitoring and control interface.

**Deliverables**:
- [ ] Strategy performance comparison
- [ ] Backtest results viewer (advanced charts)
- [ ] Live paper trading monitor
- [ ] Trade history with filters
- [ ] Performance heatmaps
- [ ] Risk metrics display
- [ ] Settings management
- [ ] User activity log (Phase 1+)
- [ ] System health dashboard

**Dependencies**: Phase 6 complete  
**Estimated Duration**: 3 weeks  
**Blockers**: None

---

## Phase 8: Machine Learning

**Goal**: Optimize strategies and detect market regimes.

**Deliverables**:
- [ ] Strategy parameter optimization
- [ ] Hyperparameter tuning
- [ ] Regime classification (ML model)
- [ ] Performance prediction
- [ ] Anomaly detection
- [ ] Strategy recommendation
- [ ] Model versioning & tracking

**Dependencies**: Phase 7 complete  
**Estimated Duration**: 4 weeks  
**Blockers**: ML expertise, data science library choice

---

## Phase 9: Production Deployment

**Goal**: Deploy to cloud infrastructure with reliability.

**Deliverables**:
- [ ] Docker image optimization
- [ ] Kubernetes manifest (if scaling)
- [ ] CI/CD pipeline (GitHub Actions)
- [ ] Production environment setup
- [ ] Database backups
- [ ] Monitoring & alerting
- [ ] Error tracking (Sentry)
- [ ] Performance profiling
- [ ] Cost optimization
- [ ] SSL/TLS certificates

**Dependencies**: Phase 8 complete  
**Estimated Duration**: 2 weeks  
**Blockers**: Cloud infrastructure decision

---

## Phase 10: Validation & Iteration

**Goal**: Extended live paper trading and refinement.

**Deliverables**:
- [ ] Live paper trading (3+ months)
- [ ] Performance analysis
- [ ] User feedback collection
- [ ] Bug fixes & optimizations
- [ ] Documentation updates
- [ ] Strategy library expansion
- [ ] Market coverage expansion
- [ ] Readiness assessment for Phase 11 (optional: live trading)

**Dependencies**: Phase 9 complete  
**Estimated Duration**: 12+ weeks  
**Blockers**: Market conditions, strategy performance

---

## Beyond Phase 10 (Optional)

### Phase 11: Live Trading (Not Planned)

- ⚠️ Advanced risk protocols
- ⚠️ Capital allocation
- ⚠️ Regulatory compliance
- ⚠️ Insurance & audit trails
- ⚠️ Professional monitoring

(This phase requires extensive testing, legal review, and capital constraints. Not planned for initial release.)

---

## Timeline Overview
