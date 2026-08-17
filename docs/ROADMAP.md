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

## Phase 2: Market Data Collection 🚧 PHASE 2D CONTINUES IN PHASE 7

**Goal**: Fetch and store historical candlestick data.

**Status**: Phases 2A–2C are implemented and operationally validated. The
approved Phase 2D operational-administration scope now continues as part of the
active Phase 7 Operational Control Plane. See
[`CHAT_CONTINUITY.md`](./CHAT_CONTINUITY.md) for the exact current delivery.

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

- [x] Plan RAW backfill
- [x] Submit RAW backfill with explicit idempotency key
- [x] Submit RAW incremental update
- [x] List and inspect RAW datasets
- [ ] Read RAW gaps and quality
- [x] List and inspect operations
- [x] Pause, resume and cancel cooperatively
- [x] Reconcile abandoned operations after restart
- [x] Expose a minimal administrator UI

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

**Status**: Scope and architecture approved. Implementation is active within
the Phase 7 control-plane track; this historical section remains the contract
for the market-data operational boundary rather than a claim of Phase 2 closure.

**Deliverables**:
- [x] Market data adapter interface
- [x] Binance adapter (OHLCV data)
- [x] Data models (Candle, OHLCV)
- [x] Parquet storage (efficient, queryable)
- [x] Multi-symbol support
- [x] Multi-timeframe support (1m, 5m, 1h, 1d)
- [x] Historical data sync job
- [x] Data validation foundations
- [x] Admin interface to trigger sync (Phase 2D)

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

## Phase 4: Optimization and Walk-Forward ✅ COMPLETE

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
- [x] **4-05 — Walk-forward**: deterministic rolling fixed windows over one
  immutable snapshot; complete 4-02/4-03 plans and verified 4-04 execution per
  fold; explicit TRAIN eligibility and VALIDATION-only metric selection;
  complete TEST-free candidate evidence with deterministic tie-break; winner
  frozen before the selected holdout is explicitly verified; no fallback after
  failed holdout; plan-bound validation and recoverable canonical
  PREPARED/COMMITTED publication. No global score or overfitting analysis is
  produced.
- [x] **4-06 — Overfitting, stability and reports**: deterministic analysis of
  the already selected holdouts; exact VALIDATION→TEST degradation,
  TEST-not-worse and completion ratios; parameter-set turnover independent of
  fold-local IDs; explicit bounded controls and separate overfitting,
  parameter-stability and aggregate assessments; canonical recomputable
  reports with atomic publication. No statistical proof, global strategy
  ranking or production recommendation is produced.

**4-01 safety limits**: 1,000 combinations by default and an absolute ceiling
of 100,000. Cardinality is validated before combinations are materialized. The
strict `REJECT_SPACE` policy rejects the complete space at the first combination
that the registered strategy factory rejects; invalid combinations are never
silently omitted.

**Explicitly deferred after Phase 4**: advanced statistical overfitting tests
(PSR/DSR, PBO, Reality Check and CPCV), purge/embargo windows, sensitivity and
Monte Carlo analysis, global strategy ranking or automatic production
promotion, multi-asset optimization, distributed workers, machine learning and
any unbounded or random search. Real-time paper trading begins only in Phase 5.

**Dependencies**: Phases 3B and 3C complete
**Blockers**: None

---

## Phase 5: Risk Management & Paper Trading ✅ COMPLETE

**Goal**: Simulate live trading with realistic constraints.

**Status**: Complete. All scoped Phase 5 implementation deliverables passed the
full repository gate, staged-diff and secret audit, and were merged to `main`.
The release is versioned by the annotated `phase-5` tag. Phase 6 is a separate
subsequent phase whose implementation and local closure sequence are now in
progress.

### Phase 5-01: live asset catalog and public market API ✅

**Scope**: Reuse the Phase 2 Binance Spot adapter behind a bounded runtime
catalog and explicit read-only HTTP contracts. The application exposes active
and inactive normalized instruments, supported ADT timeframes, source freshness
and an uncached current public price. Catalog metadata is cached for a configured
TTL with one-flight refresh; price observations are never reused as catalog data.
No API key, account endpoint, order endpoint, trading permission, database
migration or permanent worker is introduced.

**Delivered**:
- [x] Bounded immutable in-memory asset catalog with deterministic filtering and pagination
- [x] Single-flight TTL refresh from Binance Spot `exchangeInfo`
- [x] Current public price normalization through the Spot ticker-price endpoint
- [x] Public `GET /api/v1/market/assets` catalog endpoint
- [x] Public asset-detail and current-price endpoints
- [x] Decimal-string API serialization, stable errors and no-network tests

**Explicitly deferred by 5-01**: streaming/WebSocket ingestion, continuous candle
collection, strategy scheduling, simulated orders, positions, portfolio
accounting, risk vetoes, database persistence and live trading.

### Phase 5-02: bounded continuous RAW candle collection ✅

**Scope**: Maintain an explicit, bounded list of Binance Spot pair/timeframe
targets by composing the existing Phase 2B incremental planner and executor.
The collector runs outside FastAPI, serializes one cycle at a time under a
volume-wide file lock, uses the existing per-dataset transaction/lock boundary
and publishes only a small atomic latest-cycle state.

**Delivered**:
- [x] Separate `run-once` and permanent-loop collector commands
- [x] Explicit repeatable `BASE/QUOTE:TIMEFRAME` targets and bounded bootstrap
- [x] Incremental overlap only after a newly closed candle is available
- [x] Network-free `NOOP` cycles when each target is already current
- [x] Sequential failure isolation with canonical cycle and target states
- [x] Atomic local state, deterministic checksum/ID and read-only status API
- [x] One active collector per persistent `ADT_DATA_DIR` volume
- [x] Deterministic no-network tests and typed configuration limits

**Explicitly deferred after 5-02**: WebSocket streaming, dynamic target
subscriptions, PostgreSQL collector state, distributed collectors and live
trading.

### Phase 5-03: deterministic local paper-trading core ✅

**Scope**: Create immutable paper-session identities and replay each bounded
closed-candle prefix through the existing Phase 3 engine. The implementation
reuses strategy factories, lifecycle/warmup behavior, execution assumptions,
portfolio accounting, ledger and risk vetoes. A paper cycle preserves an open
order at the current data boundary and republishes a complete authenticated
latest state; ordinary backtests keep their existing terminal cancellation.

**Delivered**:
- [x] Versioned immutable paper-session configuration and deterministic ID
- [x] Lifecycle-1 and lifecycle-2 warmup enforcement
- [x] Closed RAW candle source with strict identity, continuity and checksum validation
- [x] Replay through the Phase 3 engine, portfolio, execution and risk contracts
- [x] Open-order preservation across growing replay prefixes
- [x] Canonical config/state documents with atomic local publication
- [x] State regression protection and exact source-range verification
- [x] Local `create`, `run-once`, `status` and `verify` commands
- [x] Bounded replay configuration and no-network deterministic tests

**Explicitly deferred after 5-03**: permanent session scheduling, incremental
serialization of arbitrary strategy state, HTTP mutation endpoints, dashboard,
PostgreSQL session persistence, automatic strategy promotion, notifications,
WebSocket/event streaming and live trading.

### Phase 5-04: continuous paper-session runner and read-only API ✅

**Scope**: Execute an explicit bounded set of immutable 5-03 sessions in a
separate fixed-cadence process. Each cycle is sequential and failure-isolated,
reuses the verified complete replay boundary and publishes one compact atomic
latest-cycle document. FastAPI only reads persisted runner/session state; no
request creates a session or invokes a strategy.

**Delivered**:
- [x] Separate paper `runner run-once`, `runner loop` and `runner status` commands
- [x] Explicit sorted session IDs, bounded cadence and maximum session count
- [x] Sequential `UPDATED`, `NOOP` and `FAILED` results with aggregate cycle state
- [x] One active paper runner per persistent `ADT_DATA_DIR` volume
- [x] Atomic canonical latest-cycle state with checksum and deterministic cycle ID
- [x] Read-only paginated session, order, fill and runner-status API
- [x] Decimal-string serialization and no strategy execution inside HTTP requests
- [x] Deterministic remote-free tests and typed configuration limits

**Explicitly deferred after 5-04**: dynamic session subscriptions, HTTP mutation,
incremental serialization of arbitrary strategy state, dashboard, PostgreSQL
persistence, notifications, WebSocket/event streaming, automatic promotion and
live trading.

### Phase 5-05: deterministic position sizing engine ✅

**Scope**: Convert strategy intents into bounded Spot quantities before the
existing risk manager, while preserving legacy explicit-quantity identities and
keeping the risk manager authoritative for final normalization and veto.

**Delivered**:
- [x] Immutable explicit-quantity, fixed-notional and equity-percent policies
- [x] Decimal-only adverse price, fee, quote-reserve and quantity-step projection
- [x] Buy sizing before risk validation with deterministic zero-quantity rejection
- [x] Explicit sell quantities and long-only Spot semantics preserved
- [x] Identity-bearing extended execution contract with legacy ID compatibility
- [x] Backtest and paper CLI flags plus canonical paper/experiment round trips
- [x] Deterministic remote-free sizing, engine, CLI and document tests

**Explicitly deferred after 5-05**: stop-loss enforcement, leverage, shorts,
dynamic risk allocation, exchange-account access, HTTP mutation, scheduling and
live trading.

### Phase 5-06: deterministic stop-loss enforcement ✅

**Scope**: Maintain one engine-owned full-position protective stop in deterministic
backtesting and paper replay while preserving disabled-policy identities and
reusing the existing `STOP_MARKET` execution model.

**Delivered**:
- [x] Immutable disabled and fixed-percent stop-loss policies
- [x] Weighted-average-entry trigger with Decimal-only price-tick truncation
- [x] Automatic create, replace and cancel lifecycle after position-changing fills
- [x] Deterministic touch and gap execution through the existing fill model
- [x] Drawdown-halt preservation, reserved engine tag and fail-closed order limits
- [x] Legacy-compatible backtest, paper and experiment identities and documents
- [x] Backtest and paper CLI flags plus deterministic remote-free regression tests

**Explicitly deferred after 5-06**: trailing stops, ATR/volatility stops, take-profit,
OCO groups, leverage, shorts, exchange-account access, HTTP mutation and live
trading.

### Phase 5-07: asset-level performance tracking ✅

**Scope**: Aggregate only independently verified completed backtest summaries by
canonical exchange, market type and symbol, without mutating historical run
artifacts or inventing additive interpretations for per-run ratios.

**Delivered**:
- [x] Canonical asset identity derived from validated dataset keys
- [x] Immutable per-run performance projections with source-result checksums
- [x] Deterministic grouping of 1 to 100 unique verified runs by asset
- [x] Capital-weighted return, capital, profit, run-count, trade and drawdown totals
- [x] Stable best/worst run identities and content-addressed report IDs
- [x] Atomic idempotent report publication with exact manifest/source bindings
- [x] Local generate, export, inspect and verify CLI commands
- [x] Remote-free typed tests covering canonical round trips and tamper rejection

**Explicitly deferred after 5-07**: calendar-period performance series, market
regime attribution, cross-asset portfolio accounting, strategy promotion,
PostgreSQL/API persistence, dashboards, scheduling, notifications and live
trading.

### Phase 5-08: deterministic market regime detection ✅

**Scope**: Classify every closed candle with a versioned, explainable and
no-look-ahead heuristic shared by backtesting and bounded paper replay. The policy
uses fast/slow EMA separation and normalized ATR, remains opt-in for compatibility,
and exposes regime information without changing strategy decisions.

**Delivered**:
- [x] Immutable versioned policy with trend, range, volatile and warmup regimes
- [x] Separate up/down/none trend direction and explainability metrics
- [x] Batch and constant-memory incremental calculations with exact parity
- [x] Closed-candle strategy context integration without fill-time future leakage
- [x] Identity-bearing backtest configuration with legacy compatibility
- [x] Optional authenticated `regimes.jsonl`, inspect, verification and paginated CLI
- [x] Lossless Decimal serialization independent of ambient precision
- [x] Schema-2 paper sessions with one bounded latest verified regime observation
- [x] Backtest and paper CLI policy flags plus deterministic remote-free tests

**Explicitly deferred after 5-08**: performance attribution by regime, learned or
probabilistic classifiers, automatic strategy selection/promotion, calendar-period
performance series, cross-asset portfolio accounting, PostgreSQL persistence,
notifications, exchange-account access and live trading.

### Phase 5-09: live performance dashboard ✅

**Scope**: Provide an administrator-only projection over canonical paper-session
and runner documents without adding an execution or mutation path. The backend
owns all validation and metrics; the frontend polls a bounded page and compares
at most two loaded sessions locally.

**Delivered**:
- [x] Deterministic page-scoped dashboard read model and nominal aggregate totals
- [x] Authenticated bounded `GET /api/v1/admin/paper-trading/dashboard`
- [x] Equity, PnL, drawdown, position, orders, runner and latest-regime cards
- [x] Generated OpenAPI TypeScript aliases with domain enum fidelity
- [x] Responsive `/admin/paper-trading` route with 30-second polling
- [x] Manual refresh and local comparison of up to two sessions
- [x] Backend, frontend and Playwright read-only contract regressions

**Explicitly deferred after 5-09**: trade journal, calendar-period performance
series, performance attribution by regime, cross-asset currency conversion,
WebSocket streaming, notifications, PostgreSQL paper-session persistence,
exchange-account access and live trading.

### Phase 5-10: deterministic trade journal ✅

**Scope**: Reconstruct position lifecycles from canonical verified paper-session
orders and fills, expose bounded read-only administrator queries, and provide
deterministic CSV/JSONL exports without introducing an execution path.

**Delivered**:
- [x] Verified deterministic journal reconstruction with partial exits
- [x] Open and closed trade accounting with exact Decimal values
- [x] Bounded filters, pagination and newest-first read model
- [x] Content-addressed query and export checksums
- [x] Authenticated GET-only administrator API and deterministic exports
- [x] Responsive protected trade-journal page and frontend regressions

**Explicitly deferred after 5-10**: calendar-period performance series,
performance attribution by regime, cross-asset currency conversion,
notifications, PostgreSQL paper-session persistence, exchange-account access
and live trading.

### Calendar-period performance metrics ✅

**Scope**: Project verified paper-trading exit realizations into continuous UTC
daily, ISO-weekly and Gregorian-monthly buckets for exactly one quote asset.
Partial exits are attributed at each SELL event and the service does not invent
historical mark-to-market state that is absent from persisted paper snapshots.

**Delivered**:
- [x] Exact average-cost realization allocation for every exit execution
- [x] UTC half-open daily, weekly and monthly calendar buckets
- [x] Continuous bounded series including empty periods
- [x] Realized PnL, fees, slippage, win rate and profit factor
- [x] Quote-asset isolation and deterministic source/query/content checksums
- [x] Authenticated GET-only administrator API and protected frontend page
- [x] Explicit nonclaims for historical equity, unrealized PnL and drawdown

**Operational contract**:
[`docs/PAPER_TRADING_PERIOD_METRICS.md`](./PAPER_TRADING_PERIOD_METRICS.md)

**Explicitly deferred after the Phase 5 implementation**: performance
attribution by regime, cross-asset currency conversion, historical
mark-to-market snapshots, WebSocket streaming, notifications, PostgreSQL
paper-session persistence, exchange-account access and live trading.

**Deliverables**:
- [x] Position sizing engine
- [x] Stop-loss enforcement
- [x] Asset-level performance tracking
- [x] Market regime detection (trend/range/volatile)
- [x] Paper trading engine (bounded local deterministic replay)
- [x] Live performance dashboard
- [x] Trade history & journal
- [x] Performance metrics (daily, weekly, monthly)
- [x] Risk limits & veto system (reused from Phase 3)

**Dependencies**: Phase 4 complete
**Estimated Duration**: 3 weeks
**Blockers**: None

---

## Phase 6: Advanced Frontend and Financial Charts ✅ COMPLETE

**Goal**: Turn the existing read-only administrative surfaces into a precise,
accessible and bounded visual analysis environment without weakening the
deterministic market-data, paper-trading and audit contracts delivered through
Phase 5.

**Status**: Complete. Phase 6-01 through Phase 6-07 are implemented. The
full local backend/frontend/browser matrix and deterministic closure gates
passed, the exact staged candidate was audited and approved, and the candidate
was integrated into `main` by verified fast-forward. The final release is
versioned by the annotated `phase-6` tag. Remote CI, deployment and hosted
service validation are not claimed by this local closure record.

### Phase 6-01: visual architecture and chart-data contracts ✅

- [x] Record the revised post-Phase-5 roadmap
- [x] Inventory existing and missing historical series
- [x] Define UTC, Decimal, pagination and point-limit contracts
- [x] Select the financial-chart rendering library and attribution requirements
- [x] Define administrator, authenticated-user and public presentation boundaries
- [x] Document performance, accessibility, testing and security requirements

### Phase 6-02: bounded read-only candle API ✅

- [x] Serve only persisted, closed and validated RAW candles
- [x] Filter by canonical instrument, timeframe and half-open UTC interval
- [x] Use stable bounded pagination suitable for backward chart navigation
- [x] Preserve Decimal values as strings at the HTTP boundary
- [x] Reject oversized requests instead of silently truncating or downsampling
- [x] Execute no Binance fetch, resampling or long-running work inside HTTP

### Phase 6-03: instrument and paper-session chart ✅

- [x] Candlestick rendering with responsive resize and keyboard-accessible controls
- [x] EMA and other explicitly supported indicator overlays
- [x] Verified order, fill, entry, exit and protective-stop annotations
- [x] Journal-to-chart and chart-to-journal navigation
- [x] Explicit UTC display and closed-candle freshness state
- [x] Bounded polling with deterministic replacement of chart data

### Phase 6-04: deterministic portfolio time series ✅

- [x] Produce a content-addressed portfolio timeline from verified replay events
- [x] Record quote cash, base quantity, mark price and cost basis per observation
- [x] Record realized PnL, unrealized PnL, equity, peak equity and drawdown
- [x] Bind every point to session identity, candle boundary and source checksum
- [x] Verify exact reconstruction and reject tampered or incompatible artifacts
- [x] Preserve existing latest-state and legacy session identities

**Operational contract**:
[`docs/PAPER_TRADING_PORTFOLIO_TIMELINE.md`](./PAPER_TRADING_PORTFOLIO_TIMELINE.md)

### Phase 6-05: performance visualizations ✅

- [x] Equity and drawdown curves
- [x] Cumulative realized PnL, fees and slippage
- [x] Daily, weekly and monthly realized-performance charts
- [x] Win/loss, profit-factor and trade-distribution views
- [x] Bounded heatmaps and session comparisons
- [x] Explicit labels for metrics that do not represent historical mark-to-market

**Operational contract**:
[`docs/PAPER_TRADING_PERFORMANCE_VISUALIZATIONS.md`](./PAPER_TRADING_PERFORMANCE_VISUALIZATIONS.md)

### Phase 6-06: public and authenticated-user surfaces ✅

- [x] Improve the public landing page using only intentional public projections
- [x] Establish an authenticated `/app` boundary separate from `/admin`
- [x] Define read-only authenticated views for market charts and authorized
      paper-session charts, trades and performance
- [x] Explicitly defer trading signals until an authoritative signal contract
      and artifact exist
- [x] Keep public user registration disabled
- [x] Preserve backend authentication and `app_admins` authorization as the
      authoritative access-control source
- [x] Add route, authentication, authorization, accessibility, browser and
      responsive-layout tests

**Operational contract**:
[`docs/PHASE6_USER_SURFACES.md`](./PHASE6_USER_SURFACES.md)

### Phase 6-07: integration and closure gate ✅

- [x] Closure reconnaissance and gate inventory (6-07A)
- [x] Final determinism, RAW partition integrity and persisted paper-state
      binding work (6-07B, including B1R/B2R)
- [x] Local Chromium browser integration and automated accessibility closure
      (6-07C)
- [x] Deterministic response-size and bundle baseline budgets; latency and
      memory observations remain report-only (6-07D)
- [x] Local authentication, authorization, secret, OpenAPI and documentation
      audit (6-07E)
- [x] Re-run the final full repository backend/frontend/browser matrix and
      deterministic closure gates (6-07F local gate)
- [x] Audit and approve the exact staged diff, commit the candidate, integrate
      it into `main` by verified fast-forward and create the annotated `phase-6`
      tag (6-07F release closure)

**Explicitly out of scope**:

- creating, starting, pausing or deleting collectors and paper sessions through HTTP;
- machine-learning training or automatic strategy recommendation;
- Telegram notifications;
- production cloud deployment;
- exchange-account access or real-capital order execution;
- silent chart downsampling that changes candle or financial semantics.

**Architecture document**:
[`docs/PHASE6_FRONTEND_AND_CHARTS_ARCHITECTURE.md`](./PHASE6_FRONTEND_AND_CHARTS_ARCHITECTURE.md)

**Candle API contract**:
[`docs/MARKET_CANDLE_CHART_API.md`](./MARKET_CANDLE_CHART_API.md)

**Dependencies**: Phase 5 complete
**Estimated Duration**: 4 weeks
**Blockers**: None for the completed Phase 6 boundary

---

## Phase 7: Operational Control Plane

**Goal**: Create, validate and operate market-data and paper-trading workflows
through an authenticated control plane without executing long-running work in
HTTP requests.

**Status**: Active. Tracks 7-01 — Control Plane Foundation and 7-02 — Market
Operation Administrative Console are complete and closed. Track 7-03 —
Persisted RAW Dataset Inspection is the active delivery. Phase 7 as a whole
remains active; the exact current handoff is maintained in
[`CHAT_CONTINUITY.md`](./CHAT_CONTINUITY.md).

### Phase 7 remaining deliverables
- [ ] Define administrator-approved operational mandates for assets, markets
      and instruments
- [ ] Create and validate paper-session configurations through the frontend
- [ ] Configure timeframes and trading horizons as their contracts mature
- [ ] Select strategies, sizing, fees and risk policies within explicit mandates
- [x] Submit and monitor bounded market-data synchronization operations
- [ ] Start, pause, resume and stop durable collectors and paper runners
- [x] Reconcile abandoned work after restart
- [ ] Integrate the administrative capital ledger with operational sessions
- [ ] Establish the ledger and session foundations required by a future ADT
      Official Portfolio and official paper capital without claiming that
      portfolio is already implemented
- [ ] Show worker health, leases, progress, errors and audit events
- [x] Preserve CLI workflows and explicit operator confirmation
- [x] Preserve auditable start, pause, resume, cancel and recovery transitions
- [ ] Complete the previously approved Phase 2D operational administration scope

**Dependencies**: Phase 6 complete
**Estimated Duration**: 4 weeks
**Blockers**: Reviewed PostgreSQL operational migrations and persistent worker host

### 7-02 — Market Operation Administrative Console ✅

**Status**: Complete and closed. The authenticated administrative console is
implemented, locally validated and integrated into `main`.

**Goal**: Expose the already validated 7-01 `MarketOperation` control plane
through a bounded authenticated administrative browser surface.

**Scope**:

- backend-owned valid target resolution;
- explicit operation preview;
- explicit confirmation and submission for RAW backfill and RAW incremental
  operations;
- operation list and detail views with progress, result, failure and timestamps;
- safe lease-time presentation that does not claim a worker process is online;
- pause, resume and cancel controls using the current `record_version`;
- bounded polling;
- generated frontend API contracts; and
- a protected, accessible and responsive administrator page.

**Safety contract**: No physical market-data execution occurs inside FastAPI
requests. The browser does not decide operation ownership or fabricate final
state; the existing 7-01 API, worker, local durability and CLI contracts remain
authoritative.

**Idempotency**: Ambiguous retries of the same confirmed intent preserve the
same non-sensitive idempotency key. A genuinely new intent uses a new key.

**Concurrency**: Controls use the current `record_version`. A conflict requires
reload and reconciliation rather than a blind mutation retry.

**Lease presentation**: Lease and heartbeat timestamps do not prove that a
worker process is online.

**Expected migration**: None.

**Dependencies**: 7-01 closed.

**Explicitly out of scope**:

- dataset, gap and quality inspection or repair;
- worker-global presence, health or operational events;
- collector or paper-runner lifecycle control and paper-session creation;
- administrator mandates;
- capital-ledger integration or the ADT Official Portfolio;
- machine learning, Telegram, SaaS and deployment; and
- real-capital execution.



### 7-03 — Persisted RAW Dataset Inspection 🚧

**Status**: Active / bootstrap.

**Goal**: Expose a bounded, authenticated and read-only administrative view of
locally cataloged RAW datasets without leaking storage paths and without
performing physical market-data work inside HTTP requests.

**Scope**:

- list cataloged RAW datasets with deterministic bounded pagination;
- inspect one dataset through the canonical backend-owned `dataset_id`;
- expose canonical market identity, persisted temporal coverage, candle count,
  dataset version, version algorithm and catalog update timestamp;
- expose a sanitized integrity-manifest summary without partition paths;
- generated frontend API contracts and typed client methods;
- protected, accessible and responsive administrator list/detail UI.

**Safety contract**:

- no `location`, `relative_path`, `ADT_DATA_DIR` or filesystem disclosure;
- no Binance/network access;
- no ingestion, repair, gap discovery or advanced quality scan;
- no Parquet, catalog or PostgreSQL mutation;
- no browser-side recreation of dataset identity encoding.

**Expected migration**: None.

**Dependencies**: 7-02 closed and integrated.

**Explicitly out of scope**:

- RAW gaps and quality inspection;
- repair or materialization;
- DERIVED datasets and snapshots;
- worker-global presence/events;
- collector or paper-runner lifecycle control;
- mandates, capital integration and Official Portfolio;
- machine learning, Telegram, SaaS, deployment and real-capital execution.

---

## Phase 8: Machine Learning and Recommendation

**Goal**: Add versioned, explainable and rigorously evaluated models that assist
strategy and parameter selection without silently promoting a model to live use.

**Deliverables**:
- [ ] Dataset and feature contracts with no look-ahead leakage
- [ ] Model-training and evaluation pipelines
- [ ] ML-based regime classification
- [ ] Performance and degradation prediction
- [ ] Anomaly detection
- [ ] Strategy and parameter recommendations
- [ ] Strategy and parameter intelligence by asset, timeframe and regime
- [ ] ADT Confidence Score contract, evidence model and calibrated presentation
- [ ] Robustness, consistency and degradation evidence independent of return
- [ ] Model registry, versioning, lineage and reproducibility
- [ ] Walk-forward and holdout integration with the Phase 4 evidence
- [ ] Human approval before any recommendation becomes an active paper session
- [ ] Evaluate possible autonomous PAPER orchestration inside
      administrator-approved mandates only after explicit safety and evaluation
      gates

The Confidence Score must not be presented as a probability of profit unless a
future explicitly calibrated statistical contract supports that interpretation.
Nothing in Phase 8 authorizes automatic real-capital execution.

**Dependencies**: Phase 7 complete
**Estimated Duration**: 4+ weeks
**Blockers**: Model scope, evaluation policy and data-volume assessment

---

## Phase 9: Production Deployment

**Goal**: Deploy the validated system to persistent infrastructure with
reliable process supervision, backups, monitoring and secure delivery. The
production topology must support a continuously available official paper trader
and safe public read projections without moving execution into HTTP requests.

**Deliverables**:
- [ ] Production container and environment design
- [ ] CI/CD pipeline with protected deployment gates
- [ ] Persistent backend, worker and `ADT_DATA_DIR` topology
- [ ] Supabase production configuration and reviewed migrations
- [ ] Database and artifact backups with restore testing
- [ ] Process supervision, health checks and automatic restart
- [ ] Monitoring, alerting and error tracking
- [ ] TLS, CORS, CSP, rate limits and secret management
- [ ] Performance and cost profiling
- [ ] Availability and freshness boundaries for the official paper trader
- [ ] Safe, bounded public projections for implemented ADT Live capabilities
- [ ] Kubernetes only if measured scale requires it

**Dependencies**: Phase 8 complete
**Estimated Duration**: 2+ weeks
**Blockers**: Hosting and persistent-storage decision

---

## Phase 10: Public Product and Telegram-Assisted Distribution

**Goal**: Expose the validated official paper trader as a safe public product
and distribute authorized signals for human manual execution, without granting
the product exchange-account or subscriber-capital authority.

### Phase 10A: ADT Live public product surface

- [ ] Public official-paper capital, return, equity and drawdown projections
- [ ] ADT Official Portfolio history and capital-era projections once implemented
- [ ] ADT Confidence Score projection only after its Phase 8 contract is validated
- [ ] Public operations, charts, freshness and health suitable for disclosure
- [ ] Official Telegram entry point or link

### Phase 10B: Telegram-assisted signal distribution

- [ ] Secure Telegram bot and authorized-recipient configuration
- [ ] Entry, exit, stop, invalidation and expiry notifications
- [ ] Observed price, strategy, timeframe, rationale and risk context
- [ ] Signal-specific confidence/evidence only where authoritative
- [ ] Estimated targets or horizons only when backed by an explicit model
- [ ] Signal acknowledgement and optional manual-execution recording
- [ ] Follow-up exit notifications and daily summaries
- [ ] Error, stale-data and runner-health alerts
- [ ] Idempotent delivery, retry, deduplication and audit history

### Phase 10C: Subscription and entitlement layer

- [ ] Subscriber accounts, plans, lifecycle and entitlement state
- [ ] Account-to-Telegram association and premium-signal authorization
- [ ] Price-agnostic payment-provider and billing integration
- [ ] Auditable access changes, revocation and delivery authorization

Manual execution remains the initial real-money boundary. Subscriber capital is
independent of the ADT Official Portfolio. Phase 10 uses no exchange API key and
does not automatically place real orders.

**Dependencies**: Phase 9 complete
**Estimated Duration**: 1–2 weeks
**Blockers**: Telegram bot token and approved signal contract

---

## Phase 11: Extended Validation and Iteration

**Goal**: Run the deployed system for at least three months with paper trading
and optional manually executed Telegram signals before any real-automation
assessment.

**Deliverables**:
- [ ] Continuous paper trading for 3+ months
- [ ] Signal-delivery and operator-response analysis
- [ ] Simulated-versus-manual execution comparison
- [ ] Strategy stability and regime analysis
- [ ] ADT Official Portfolio track-record assessment across immutable capital eras
- [ ] Confidence Score calibration and usefulness assessment
- [ ] Subscriber-signal reliability assessment where distribution is implemented
- [ ] Compare official paper results with optional manually replicated execution
- [ ] Failure, restart, stale-data and alert reliability review
- [ ] Bug fixes, performance improvements and documentation updates
- [ ] Strategy-library and market-coverage expansion
- [ ] Formal readiness assessment for optional Phase 12

**Dependencies**: Phase 10 complete
**Estimated Duration**: 12+ weeks
**Blockers**: Observation time, market conditions and operational evidence

---

## Beyond Phase 11 (Optional)

### Phase 12: Automated Real-Capital Trading (Not Planned)

This phase is not part of the initial ADT release and must not begin merely
because Phase 11 completes.

Potential prerequisites include:

- ⚠️ legal and regulatory review;
- ⚠️ exchange-account and API-key isolation;
- ⚠️ strict capital allocation and loss limits;
- ⚠️ independent order and balance reconciliation;
- ⚠️ duplicate-order and restart protection;
- ⚠️ emergency shutdown and human override;
- ⚠️ professional monitoring and incident response;
- ⚠️ extended audited evidence from manual-assisted operation.

The project may remain permanently in Telegram-assisted manual execution without
ever implementing automated real-capital trading.

---

## Timeline Overview
