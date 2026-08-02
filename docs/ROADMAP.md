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

## Phase 2: Market Data Collection 🚧 PHASE 2D PLANNED

**Goal**: Fetch and store historical candlestick data.

**Status**: Phases 2A–2C are implemented and operationally validated. Phase 2D
is formally scoped and remains to be implemented before Phase 2 can close.

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

### Phase 2D: market-data operational administration 🔵 APPROVED

**Goal**: Allow authenticated administrators to plan, submit and monitor
asynchronous RAW market-data synchronization while preserving the local
durability and dataset contracts established in Phases 2A–2C.

**Scope**:

- authenticated administrative API;
- PostgreSQL operational catalog for administrative intent and public job state;
- one durable market-data worker, separate from the HTTP process;
- RAW backfill planning and submission;
- RAW incremental update submission;
- RAW dataset listing and inspection;
- read-only gap and quality inspection;
- operation progress, result, pause, resume and cooperative cancellation;
- minimal administrative frontend;
- restart recovery, reconciliation, heartbeat and sanitized observability.

**Approved topology**:

- one operational host;
- one persistent POSIX `ADT_DATA_DIR`;
- API and worker as separate processes on the same host or exact same volume;
- at most one active market-data worker per volume;
- PostgreSQL coordinates queue, claim, lease, administrative state and
  idempotency;
- local Parquet, catalog, jobs, receipts, journals and `flock` remain
  authoritative for dataset contents and execution durability.

**Worker contract**:

- claim with `FOR UPDATE SKIP LOCKED` or equivalent;
- finish the claim transaction before network, local locks or filesystem work;
- execute at most one operation at a time;
- maintain a bounded lease and heartbeat;
- observe pause and cancellation only at safe cooperative boundaries;
- recover abandoned operations and reconcile PostgreSQL from durable local
  state;
- support permanent `run` and bounded `run-once` CLI modes;
- shut down cleanly on `SIGTERM` and `SIGINT`.

**MVP operations**:

- [ ] Plan RAW backfill
- [ ] Submit RAW backfill with explicit idempotency key
- [ ] Submit RAW incremental update
- [ ] List and inspect RAW datasets
- [ ] Read RAW gaps and quality
- [ ] List and inspect operations
- [ ] Pause, resume and cancel cooperatively
- [ ] Reconcile abandoned operations after restart
- [ ] Expose a minimal administrator UI

**Explicitly out of scope**:

- DERIVED materialization and snapshot operations through the API;
- periodic scheduling or automatic unsupervised submission;
- PostgreSQL candle storage;
- distributed storage or cross-host file coordination;
- multiple active workers per volume;
- strategies, indicators, rule engines and backtests;
- non-crypto calendars, additional adapters and real-capital trading.

**Retention**: Operations and events are retained for 30 days by policy. Phase
2D does not implement automatic cleanup or a retention scheduler.

**Completion criteria**:

- [ ] A reviewed migration creates the operational catalog with RLS enabled and
  no Data API access
- [ ] Same idempotency key and payload return the same operation; divergent
  payload returns a conflict
- [ ] HTTP requests never execute long-running market-data work
- [ ] API and worker have separate process lifecycles
- [ ] No PostgreSQL transaction remains open during network, `flock`, Parquet or
  `fsync`
- [ ] Only one operation per dataset and one operation per worker execute at a
  time
- [ ] Pause and cancellation are observed only at documented safe boundaries
- [ ] Crash recovery preserves committed chunks and never refetches a confirmed
  receipt
- [ ] A durable local commit is required before PostgreSQL reports `COMPLETED`
- [ ] `COMMITTED` journal state remains successful across cleanup failure
- [ ] CLI local workflows remain available without Supabase configuration
- [ ] RAW, DERIVED and snapshot formats remain compatible
- [ ] Administrative API, PostgreSQL, worker, frontend and recovery tests pass
- [ ] Operational validation covers restart, reconciliation and clean shutdown

**Architecture decision**:
[`docs/adr/0001-phase-2d-operational-market-data-control-plane.md`](./adr/0001-phase-2d-operational-market-data-control-plane.md)

**Status**: Scope and architecture approved; implementation has not started.

**Deliverables**:
- [x] Market data adapter interface
- [x] Binance adapter (OHLCV data)
- [x] Data models (Candle, OHLCV)
- [x] Parquet storage (efficient, queryable)
- [x] Multi-symbol support
- [x] Multi-timeframe support (1m, 5m, 1h, 1d)
- [x] Historical data sync job
- [x] Data validation foundations
- [ ] Admin interface to trigger sync (Phase 2D)

**Dependencies**: Phase 1 complete  
**Estimated Duration**: 2 weeks  
**Blockers**: Market data API access

---

## Phase 3: Deterministic Backtesting and Strategy Foundations 🚧

**Goal**: Establish reproducible snapshot-based simulation before production
strategy development.

### Phase 3A: deterministic candle engine ✅ IMPLEMENTED LOCALLY

**Delivered**:

- [x] Immutable Phase 2C snapshot input and final TOCTOU verification
- [x] Candle-by-candle cycle with no same-candle strategy fill
- [x] MARKET, LIMIT and STOP_MARKET with GTC, IOC and UTC DAY
- [x] Decimal-only fees, slippage, precision and Spot long-only accounting
- [x] External risk validation and drawdown halt
- [x] Append-only chained local ledger
- [x] Closed trades and deterministic Phase 3A metrics
- [x] Atomic immutable artifacts and independent verifier
- [x] Explicit safe strategy registry and network-free CLI
- [x] Local unit/integration tests and documentation

**Status**: Implemented, validated and versioned as `phase-3a`.

### Phase 3B: advanced metrics, comparison and reports ✅ IMPLEMENTED AND VERSIONED

- [x] Sharpe, Sortino, CAGR and period normalization
- [x] Comparative reports and bounded result visualization contracts
- [x] Batch comparison without parameter optimization
- [x] Export and report schemas

**Status**: Implemented, validated and versioned as `phase-3b`.

### Phase 3C: strategy and indicator framework ✅ IMPLEMENTED AND VERSIONED

- [x] Production strategy plugin lifecycle and versioning
- [x] Indicator library (RSI, EMA, MACD, Bollinger Bands, ATR)
- [x] Parameter schemas and strategy CRUD
- [x] Example indicator strategies, clearly non-financial
- [x] Strategy validation and compatibility contracts

**Status**: Deterministic indicators, versioned strategy-plugin contracts,
revisioned strategy-definition CRUD, PostgreSQL persistence and the authenticated
administrative HTTP boundary are implemented, validated and versioned as Phase 3C.

**Explicitly deferred**: optimization, walk-forward, multi-asset portfolios,
real-time paper trading, market-regime selection and machine learning.

**Dependencies**: Phase 2C immutable snapshots
**Blockers**: None for local validation

---

## Phase 4: Optimization and Walk-Forward 🚧

**Goal**: Evaluate parameter stability only after Phase 3B/3C contracts exist.

**Deliveries**:

- [x] **4-01 — Deterministic parameter-search contracts**: explicit discrete
  `bool`, `int`, `Decimal` and `str` values; fixed parameters; canonical schema
  v1 documents; SHA-256 checksums and IDs; bounded Cartesian expansion; strict
  factory validation. This delivery executes no backtest.
- [x] **4-02 — Temporal segmentation**: versioned deterministic
  `CONTIGUOUS_THREE_WAY` contracts over immutable STRICT snapshots, with exact
  candle counts, half-open UTC evaluation ranges, retrospective warmup context,
  canonical SHA-256 checksums/IDs and strict document round-trip. This delivery
  executes no backtest.
- [x] **4-03 — Reproducible experiment planning**: pure immutable manifests
  binding one legitimate snapshot, one three-way temporal plan, one finite
  parameter space, registered plugin identity, deterministic backtest settings
  and every ordered combination-by-segment planned run. TEST is explicitly a
  final holdout and this delivery executes no backtest. Planning defaults to
  3,000 runs, has a conservative absolute ceiling of 30,000, validates all
  structure/configuration before factories and documents runs through compact
  canonical combination/segment references.
- [x] **4-04 — Experiment executor**: bounded local sequential execution in
  canonical plan order; stateful observation-only retrospective warmup; explicit
  context/evaluation result ranges; evaluation-bounded semantic verification;
  preflight manifest sizing; fresh plugin and engine state per spec; verified
  Phase 3A artifact publication/reuse; plan-reconciled
  `CONTINUE_AFTER_FAILURE` records; and atomic versioned execution manifests.
  Lifecycle 1 remains the four-callback legacy contract, lifecycle 2 adds the
  warmup callback, and positive warmup requires version 2. The built-ins retain
  `no-op@1`/`ema-cross-example@1` for lifecycle-1 document compatibility and
  expose separate `@2` lifecycle-2 identities. Plugin version and lifecycle are
  distinct identity dimensions; the resolved lifecycle is carried by the
  evaluation config, run ID and artifact manifest, preventing cross-lifecycle
  reuse even for equal custom plugin name/version while preserving legacy
  Phase 3A config bytes and run IDs. Publication performs
  bounded PREPARED/COMMITTED verification; manifest reconciliation recalculates
  run IDs from the exact snapshot; published COMPLETE/REUSED artifacts have an
  explicit independent verification frontier.
- [ ] **4-05 — Walk-forward**: deterministic rolling evaluation.
- [ ] **4-06 — Overfitting, stability and reports**: out-of-sample comparison and
  explicit stability controls.

**4-01 safety limits**: 1,000 combinations by default and an absolute ceiling
of 100,000. Cardinality is validated before combinations are materialized. The
strict `REJECT_SPACE` policy rejects the complete space at the first combination
that the registered strategy factory rejects; invalid combinations are never
silently omitted.

**Explicitly deferred after 4-04**: selection/ranking, walk-forward, rolling
windows, purge/embargo windows,
optimization reports, real-time paper trading, multi-asset portfolios,
distributed workers, machine learning and any unbounded or random search.

**Dependencies**: Phases 3B and 3C complete
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
