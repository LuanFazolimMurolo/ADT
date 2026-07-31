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

## Phase 1: Supabase & Administration ⏳ HOMOLOGATION

**Goal**: Add database, user authentication, and admin dashboard skeleton.

**Status**: Phases 1A–1D are implemented and validated locally. Formal Phase 1
closure remains pending until the two reviewed migrations are applied to the
selected Supabase project and the manual homologation checklist passes. No
remote operation was performed during Phase 1D.

**Deliverables**:
- [ ] Supabase project setup
- [x] PostgreSQL administration and paper-simulation schema
- [x] Database migrations
- [x] JWT authentication (Supabase Auth)
- [x] Data API privilege boundary and public view
- [x] Admin login page (frontend)
- [x] Admin dashboard skeleton
- [x] Database-backed administrator authorization middleware
- [x] Remote-free browser E2E and vertical PostgreSQL integration
- [x] HTTP security, request correlation and health/readiness
- [ ] Apply migrations, bootstrap the first admin and homologate the real project

Strategy CRUD belongs to Phase 3, after market-data foundations in Phase 2.

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

### Phase 1C: Frontend authentication and administration ✅

**Scope**: Add the private React administration surface while keeping Supabase
Auth as the identity provider and FastAPI as the sole administrative
authority.

**Deliverables**:
- [x] Public configuration validation and single Supabase client
- [x] Login, session restoration, token renewal, logout and route protection
- [x] Password recovery and reset flows
- [x] Backend-authorized dashboard and responsive administrative layout
- [x] Paginated simulations, immutable movements and terminal transitions
- [x] Non-secret settings editor
- [x] Typed centralized API client with safe error handling
- [x] Mocked frontend tests with no remote service access
- [x] Frontend operations and Supabase Redirect URL documentation

**Status**: Complete locally. Real Supabase credentials, allowed Redirect URLs
and end-to-end validation against the selected project remain manual.

**Explicitly deferred**: Public registration, strategy CRUD, market data,
backtesting, Telegram, machine learning, real-capital trading and remote
Supabase changes.

---

### Phase 1D: Integration, E2E, security and closure gate 🟡 HOMOLOGATION

**Scope**: Verify the complete Phase 1 boundary, remove contract drift, exercise
authentication/UI flows without remote services, harden the Data API and HTTP
surface, and define the operational closure gate.

**Deliverables**:
- [x] OpenAPI-generated TypeScript contracts and strict Decimal string boundary
- [x] Safe 401/403 session invalidation, refresh and password-recovery flows
- [x] Playwright with real Supabase browser SDK and deny-by-default local mocks
- [x] 27 public/auth/admin/resilience/accessibility E2E scenarios
- [x] Signed-JWT → JWKS mock → app_admins → temporary PostgreSQL vertical test
- [x] Complete temporary-PostgreSQL financial and privilege test matrix
- [x] Data API hardening migration with one-way lifecycle defense in depth
- [x] CORS, security headers, request IDs, structured logs and body/token limits
- [x] Liveness, database and readiness separation
- [x] Security and homologation documentation
- [ ] Apply both reviewed migrations to the selected remote project
- [ ] Configure Redirect URLs, production CORS/CSP/TLS/rate limits
- [ ] Bootstrap the first administrator and execute manual homologation

**Status**: Implementation and automated local gate are available. Re-run the
entire gate, including Playwright, on the candidate commit. Overall Phase 1
intentionally remains in homologation until that evidence and the three manual
items above are complete.

**Closure checklist**:
[`docs/PHASE1_HOMOLOGATION.md`](./PHASE1_HOMOLOGATION.md)

**Explicitly deferred**: strategy CRUD/execution, market data, backtesting,
Telegram, machine learning and real-capital trading.

---

## Phase 2: Market Data Collection

**Goal**: Fetch and store historical candlestick data.

### Phase 2A: local historical-data foundation 🟡 IMPLEMENTED LOCALLY

**Scope**: Canonical market models, configurable timeframes, a public Binance
Spot adapter, quality validation, monthly Parquet storage, local operational
catalog and an explicit CLI. No strategy, backtest, order, permanent worker or
remote Supabase dependency is included.

**Delivered locally**:

- [x] Adapter protocol and Binance Spot public REST adapter
- [x] Canonical UTC/Decimal candle and instrument models
- [x] Configured 1m, 5m, 15m, 30m, 1h, 4h and 1d timeframes
- [x] Bounded reusable public HTTP client with retry/rate-limit handling
- [x] Journaled Parquet/catalog transaction with idempotent crash recovery
- [x] Duplicate, order, gap, alignment, OHLCV, overlap and open-candle checks
- [x] Transactional local dataset/ingestion catalog
- [x] Local instruments/fetch/inspect/verify CLI
- [x] Deterministic no-network automated tests and opt-in minimal smoke test

**Deferred**: scheduled workers, administrator API/UI, additional market
adapters and a PostgreSQL operational catalog. The latter
should be introduced only with a reviewed migration when multi-process
orchestration requires it.

### Phase 2B: resumable local synchronization 🟡 IMPLEMENTED LOCALLY

**Scope**: Deterministic large-range planning, sequential chunk execution,
atomic checkpoints, same-host per-dataset locking, incremental overlap, gap
discovery/repair and an operator CLI. It reuses the complete Phase 2A
transaction for every chunk.

**Delivered locally**:

- [x] Exact integer planner bounded by adapter, request, total and chunk limits
- [x] Immutable local job records with atomic progress checkpoints
- [x] Safe resume after interruption and sanitized abandoned-job recovery
- [x] Same-dataset advisory file lock with timeout
- [x] Global catalog serialization and transaction-bound chunk receipts
- [x] Incremental updates with overlap and deterministic `NOOP`
- [x] Read-only gap discovery and explicit verified repair
- [x] Full logical-content dataset version independent of chunk order
- [x] Backfill plan/run/resume/status, update, gaps and repair CLI
- [x] Deterministic no-network tests

**Deferred**: scheduling, permanent workers, distributed coordination,
administrator API/UI and additional adapters.

### Phase 2C: deterministic backtest datasets 🟡 IMPLEMENTED LOCALLY

**Scope**: Offline quality audits, exact Decimal resampling, transactional
derived datasets, durable lineage manifests, immutable local snapshots and a
lazy read-only interface for a future backtest engine.

**Delivered locally**:

- [x] Deterministic FULL and checksum-based INCREMENTAL quality scans
- [x] Explicit continuous UTC 24/7 calendar and supported timeframe matrix
- [x] STRICT, SKIP_INCOMPLETE and evaluation-only MARK_INCOMPLETE gap policies
- [x] Separate journaled derived Parquet datasets with stable logical versions
- [x] Source partition lineage, stale detection and atomic manifests
- [x] Idempotent hard-link snapshots and lazy half-open interval reader
- [x] Quality, resample and snapshot CLI commands with bounded dry-runs
- [x] Local no-network tests for resampling, recovery, manifests and snapshots

**Deferred**: the backtest engine itself, strategies, indicators, schedulers,
distributed locks, non-crypto calendars and remote object-storage snapshots.

**Deliverables**:
- [x] Market data adapter interface
- [x] Binance adapter (OHLCV data)
- [x] Data models (Candle, OHLCV)
- [x] Parquet storage (efficient, queryable)
- [x] Multi-symbol support
- [x] Multi-timeframe support (1m, 5m, 1h, 1d)
- [x] Historical data sync job
- [x] Data validation foundations
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
