# ADT Architecture

## Overview

ADT follows a **layered, modular architecture** with clear separation of concerns.

The backend is the only application component allowed to perform administrative
writes. The frontend remains a presentation layer and sends the Supabase access
token in the standard Bearer header.

```text
React client
    │ Authorization: Bearer <Supabase access token>
    ▼
FastAPI routes
    ▼
authentication and administrator dependencies
    ▼
application services
    ▼
repositories
    ▼
async psycopg connection pool
    ▼
PostgreSQL / Supabase
```

The Supabase Data API is not an alternative administrative path. Roles
`anon`, `authenticated` and `service_role` have no privileges on Phase 1 base
tables after the Phase 1D hardening migration. The only browser-readable
database object is the narrow owner-rights
`active_simulation_summary` view. FastAPI uses a secret direct PostgreSQL URL
and is the sole administrative reader/writer.

## Backend layers

The FastAPI backend is split into the following boundaries:

- `app/api/routes/`: HTTP paths, status codes and dependency composition;
- `app/api/dependencies/`: request authentication and administrator checks;
- `app/api/schemas/`: explicit request and response contracts;
- `app/auth/`: local JWT verification against the Supabase JWKS endpoint;
- `app/services/`: transaction-level application use cases;
- `app/repositories/`: parameterized SQL and row mapping;
- `app/domain/`: typed entities, enums and safe domain errors;
- `app/database/`: asynchronous pool lifecycle and transaction contexts;
- `app/core/`: typed configuration, request correlation and JSON logging;
- `app/middleware/`: body limits, security headers and safe request telemetry.

Routes do not contain SQL or financial rules. Repositories do not know about
FastAPI, and domain/services do not return HTTP responses.

## Authentication and authorization

Supabase Auth is the identity provider. The backend validates access tokens
locally with asymmetric public keys from the project's JWKS endpoint. Signature,
issuer, `authenticated` audience and expiration are mandatory. Accepted user
identity comes only from the verified `sub` UUID.

The publishable key is configuration, not proof of administrator access. The
Supabase secret key is not used by this backend.

Administrative authorization is database-backed: after authentication, a
dependency queries `public.app_admins` for the verified UUID. JWT metadata is
never treated as an administrator grant. Missing or invalid authentication
returns 401; an authenticated UUID absent from the allow-list returns 403.

## Persistence and transactions

The backend uses asynchronous psycopg connections from a bounded pool.
Application services open explicit transactions and pass one connection through
all repositories involved in a use case. Pool startup and shutdown follow the
FastAPI lifespan.

Creating a simulation and its `INITIAL_CAPITAL` ledger entry is one transaction.
Movement metadata is stored as the immutable audit record associated with the
new movement. Settings updates also record the acting administrator.

Financial correctness remains authoritative in PostgreSQL:

- all amounts are `numeric` in PostgreSQL and `Decimal` in Python;
- the unique active simulation and unique initial capital are database indexes;
- the initial-capital and non-negative-balance rules are database triggers;
- ledgers and historical simulation fields are protected by database triggers.
- terminal simulations cannot be reopened and cannot receive new movements.

Python validation improves client feedback but never replaces these constraints.
Known PostgreSQL violations are translated to stable, non-sensitive domain
errors; raw SQL messages and connection details are never returned to clients.

## Public data boundary

The public simulation endpoint reads only
`public.active_simulation_summary`. Its response schema deliberately omits the
view's internal simulation UUID and cannot expose administrator, audit or
movement-level data.

## Contract source

Pydantic request/response models generate the OpenAPI document. The document
declares success schemas, the normalized `ErrorResponse` for 400, 401, 403,
404, 409, 413, 422, 500 and 503, and `X-Request-ID` on every response.

`services/backend/scripts/export_openapi.py` creates a deterministic contract
using only fictitious environment values. `openapi-typescript` generates
`apps/web/src/types/openapi.generated.ts`; application-facing aliases import
those generated schemas. Decimal inputs are ordinary base-10 strings and
decimal outputs remain strings. JSON `null` is distinct from an absent or empty
HTTP body.

## Phase 1C frontend

The React application keeps the public site at `/` and exposes no registration
or visible login entry there. Administrative authentication starts only under
`/admin`. The frontend is split into these boundaries:

- `config/` validates the three public `VITE_*` variables without echoing
  configured values;
- `lib/supabase.ts` is the single Supabase client, with SDK-managed session
  persistence, automatic token refresh and URL session detection;
- `auth/` restores the session, confirms every administrative session through
  `GET /api/v1/admin/me`, protects routes and performs logout;
- `http/` is the only FastAPI client and attaches the current Supabase access
  token to administrative requests;
- `pages/admin/` renders backend contracts without performing financial
  calculations or direct database access.

The API client retries a failed authentication only for idempotent `GET`
requests, after asking the Supabase SDK to refresh the session. It never
automatically repeats `POST` or `PATCH`, preventing duplicate simulations,
ledger entries or setting updates. A persistent 401 ends the local session;
401/403 during administrator verification denies the private route.

Network failures are normalized without exposing URLs. A 2xx response with an
empty body is rejected because every current endpoint has a JSON contract; the
public simulation uses the explicit JSON literal `null` when there is no active
run. Response request IDs are attached to safe user-facing diagnostics.

Financial decimals remain strings across the JSON boundary. Withdrawal signs
are mapped to the backend request contract, adjustments preserve the explicit
sign, and balances/P&L displayed by the UI are always values calculated and
returned by the backend.

## Observability and HTTP security

The backend assigns or validates a UUID correlation ID and emits one structured
completion log containing only method, path, status, duration and request ID.
Known failures add a stable error code; unexpected failures log only their
exception type. Authorization headers, bodies, tokens, SQL errors and
connection strings are never log fields.

CORS accepts only configured origins, the four required methods and minimal
headers. Production rejects HTTP, localhost and loopback variants. API
responses use restrictive CSP, anti-framing, permissions, referrer and MIME
headers; administrative responses are not cacheable. Development API docs
receive a separate compatible CSP, while production disables the docs and
enables HSTS. A 1 MiB application body limit is defense in depth; distributed
rate limits remain an edge concern.

`/health` is liveness. `/health/database` is an explicit dependency probe,
`/health/readiness` is the traffic gate, and `/api/v1/system/status` exposes
only version/environment/time. JWKS is an on-demand authentication dependency.

The frontend host must set its own CSP/HSTS. React renders untrusted strings as
text, no raw HTML rendering is used, production source maps are disabled, and
only documented public `VITE_*` values may enter the bundle.

## Local validation topology

Two complementary remote-free paths are intentional:

```text
Pytest vertical integration
signed local JWT → mocked JWKS → real FastAPI dependencies
                 → real app_admins/services → disposable PostgreSQL

Playwright browser integration
Chromium → Vite → real Supabase browser SDK + real API client
        → deny-by-default loopback Auth/FastAPI network mocks
```

The PostgreSQL suite independently proves transactionality, immutable ledger,
balance/P&L, concurrency, lifecycle, privilege matrix and public view. The
browser suite proves session behavior and UI workflows without duplicating
financial calculations.

## Out of scope after Phase 1

Phase 1 does not add strategy execution, market data, backtesting, market
adapters, Telegram integration, machine learning or real-capital trading.

## Phase 2A market-data boundary

Phase 2A adds a backend-only pipeline that does not alter the Phase 1 HTTP,
authentication, simulation or ledger paths:

```text
CLI / future worker
    → MarketDataAdapter protocol
    → BinanceSpotAdapter (public REST only)
    → canonical Decimal + UTC CandleBatch
    → MarketDataQualityValidator
    → MarketDataTransactionCoordinator (persistent PREPARED/COMMITTED journal)
      ↳ ParquetCandleStore (monthly atomic partitions)
      ↳ JsonMarketDataCatalog (small atomic operational manifest)
```

`app/market_data/domain.py` owns exchange-independent types. Timeframes live in
a registry with duration, alignment and per-exchange mapping. Adapters are the
only modules allowed to understand native symbols, payload positions or source
interval codes. The reusable public HTTP client is separate from the Supabase
JWKS client and exposes bounded retries, connection limits, correlation IDs and
safe metrics.

Candles are not stored in PostgreSQL. `ADT_DATA_DIR/market` contains explicit
Parquet schemas and a small local catalog. Collision-free base/quote path
components are checked for lexical and resolved containment, and Parquet row
identity, strict ordering, unique keys and partition month/year are verified on
read. Upserts reject conflicting duplicate keys and values that would require
Decimal or timestamp truncation. Open candles may appear in diagnostics but
never cross the persistent-storage boundary.

A persistent journal coordinates temporary files, partition backups and the
catalog backup. Startup rolls back every `PREPARED` transaction and retains
every `COMMITTED` transaction before cleaning its artifacts, so recovery does
not infer commit state from the presence of a promoted target. Once
`COMMITTED` is durably fsynced, cleanup is recoverable maintenance and cannot
downgrade the completed ingestion result.

The journal also records its dataset identity and the previous/intended values
of that dataset, ingestion run and optional chunk receipt. Late `PREPARED`
recovery reverts only those owned keys and rejects any current value outside
the recorded previous/intended pair. Catalog promotion preserves the old file
through a durable hard link before atomically replacing the target.

No Phase 2A API route or permanent worker exists. Network use is operator
initiated through the CLI, and all automated adapter tests inject an in-memory
transport.

## Phase 2B local orchestration boundary

Phase 2B adds a pure planner, an atomic local job checkpoint catalog and a
sequential executor above the Phase 2A ingestion service. Each bounded chunk is
still independently committed by `MarketDataTransactionCoordinator`; job
progress advances only after that durable boundary. A per-dataset `flock`
prevents same-host writers from running concurrently, while immutable job plans
allow failed or paused work to resume at the first unconfirmed chunk.

The dataset lock is represented by an explicit validated lease and covers every
persistent caller, including direct fetches. A distinct global
`market/.catalog.lock` serializes the main catalog. Completion acquires that
catalog lease before rereading state and retains it through the durable journal
commit, preventing different datasets from losing each other's metadata.
Immutable chunk receipts are part of the same catalog replacement and allow a
post-commit/pre-checkpoint crash to recover the original metrics without
refetching.

Persistent operations recover their dataset immediately after taking its
lease, before source metadata, candle fetches, local reads or run creation.
Inspect, verify, incremental planning and gap discovery use that same exclusive
lease in Phase 2B. The global order is dataset lock, catalog lock, then files;
callers reuse an existing lease instead of nesting another `flock`.

Incremental planning uses a configured overlap and gap repair is explicit.
Discovery never mutates storage, repairs never synthesize candles, and the
executor validates the repaired logical interval. The dataset version hashes
the complete canonical logical content, so it does not depend on chunk order.
This layer has no route, scheduler, permanent service, PostgreSQL migration or
frontend dependency.

## Phase 2C derived-dataset boundary

Phase 2C is an offline layer above persisted RAW Parquet. It never calls an
adapter and never modifies RAW:

```text
RAW Parquet + catalog version
    → streaming quality scan
    → continuous UTC calendar + deterministic Decimal resampler
    → PREPARED/COMMITTED derived journal
    → derived Parquet partitions + checksummed lineage manifest
    → immutable hard-link snapshot
    → lazy MarketDatasetReader
```

Dataset locks are always acquired by sorted canonical key. Operations needing
both source and target acquire RAW and DERIVED together; catalog access remains
inside that boundary. Snapshot publication additionally uses a canonical
snapshot key. Files, manifest and journal are promoted only after durable
temporary writes. PREPARED recovery rolls every target back; COMMITTED recovery
keeps promoted targets and only removes backups.

RAW logical versions use the named `raw-partition-canonical-sha256-v1`
algorithm: each monthly partition hashes its ordered canonical candle bytes,
then the dataset version hashes the ordered `(relative path, partition hash)`
pairs. Storage plans, catalog metadata, chunk receipts, quality baselines and
derived lineage carry that same value and algorithm marker. Catalogs without
the marker are decoded as `raw-canonical-stream-sha256-legacy`; FULL quality can
audit that legacy form, while INCREMENTAL rejects it until a subsequent logical
write explicitly migrates the dataset to the composable algorithm.

The current RAW layout remains at its Phase 2A path for compatibility. Derived
data is isolated below `market/derived`, and snapshots below
`market/snapshots`. A later RAW `market/raw` migration must be explicit and
versioned; Phase 2C never moves existing files silently.

Snapshots use same-filesystem hard links so later atomic replacement of a
derived partition cannot change the old inode. This is a local-filesystem
contract, not a distributed/object-store snapshot protocol.

## Phase 2D operational administration boundary

Phase 2D adds an authenticated control plane around RAW operations without
moving candle or execution durability into PostgreSQL. The HTTP process never
executes market-data jobs:

```text
Administrator browser
    → Supabase JWT
    → FastAPI administrator dependency
    → market-data application service
    → PostgreSQL operational catalog
          │
          │ short claim transaction
          ▼
    separate market-data worker
    → existing planner and BackfillExecutor
    → dataset flock
    → local jobs.json, receipts and transaction journals
    → RAW Parquet + catalog.json
          │
          └→ sanitized progress/result reconciliation → PostgreSQL
```

The approved deployment topology is one operational host and one persistent
POSIX `ADT_DATA_DIR`. API and worker are separate processes, but both must see
the exact same volume. Only one market-data worker may be active for a volume.
PostgreSQL claim semantics prevent duplicate queue ownership, while the
existing dataset `flock` remains the authority that prevents concurrent local
writers. Neither mechanism is presented as cross-host filesystem coordination.

### Source-of-truth split

PostgreSQL is authoritative for administrative intent, submission
idempotency, requester identity, externally visible operation state, worker
claim, lease, heartbeat, cooperative control requests, sanitized progress and
sanitized results.

Local storage is authoritative for candles, dataset metadata, the effective
plan, chunk checkpoints, receipts, journals, Parquet commits and execution
recovery. PostgreSQL stores no candle payload and does not replace
`catalog.json`, `jobs.json`, receipts, journals or `flock`.

The separation is deliberate. An operation can have stale PostgreSQL progress
after a crash while already having a durable local chunk. Reconciliation must
advance PostgreSQL from the verified local state; it must never discard that
state or issue a new source request for a confirmed receipt.

### Submission and idempotency

Every mutating submission requires an explicit, non-sensitive idempotency key.
The canonical operation type, exchange, market type, symbol, timeframe,
half-open UTC interval and plan checksum form the payload identity. Repeating
the same key and identity returns the existing operation. Reusing the key with
a different identity is a conflict.

The key is scoped to the requesting administrator. PostgreSQL enforces
uniqueness on `(requested_by, idempotency_key)`, so retries by one administrator
remain deterministic without creating cross-administrator collisions or
revealing another administrator's operation.

The API validates closed enums and configured candle/chunk/range limits before
inserting an operation. Clients cannot submit paths, native exchange URLs,
manifests or job records. Planning is read-only. Submission persists intent in
a short PostgreSQL transaction and returns without waiting for Binance,
filesystem locks or Parquet.

### Claim and execution

The worker claims one eligible operation with `FOR UPDATE SKIP LOCKED` or an
equivalent atomic statement. Claim records bounded lease ownership and
heartbeat, then commits before the worker:

1. reads or recovers local execution state;
2. acquires a dataset lease;
3. performs any public Binance request;
4. processes candles;
5. writes Parquet, checkpoints or journals; or
6. performs `fsync`.

The initial worker concurrency is exactly one operation. A permanent `run`
mode and a bounded `run-once` mode share the same execution and recovery path.
`SIGTERM` and `SIGINT` stop claiming work, allow the current critical local
transaction to reach a durable boundary, publish the latest safe state and
close database/network resources.

No PostgreSQL transaction remains open while the worker waits for a local lock,
network response or filesystem durability. The established local order remains
dataset lock, catalog lock, then files. Phase 2D does not introduce a lock
order that starts in PostgreSQL and remains held across that sequence.

### Operation state machine

The persisted states are:

- `PENDING`: durable administrative intent, not owned by a worker;
- `CLAIMED`: worker lease acquired, local reconciliation not yet complete;
- `RUNNING`: local state reconciled and execution active;
- `PAUSE_REQUESTED`: an administrator requested a cooperative pause;
- `PAUSED`: execution stopped at a safe boundary and may be resumed;
- `CANCEL_REQUESTED`: an administrator requested cooperative cancellation;
- `CANCELLED`: execution stopped at a safe boundary and will not resume;
- `COMPLETED`: local result is durably committed and reconciled;
- `FAILED`: terminal sanitized failure;
- `RECOVERING`: an expired/abandoned lease is being reconciled with local state.

The allowed transition matrix is:

| Current state | Allowed next states |
|---|---|
| `PENDING` | `CLAIMED`, `PAUSE_REQUESTED`, `CANCEL_REQUESTED` |
| `CLAIMED` | `RUNNING`, `RECOVERING`, `PAUSE_REQUESTED`, `CANCEL_REQUESTED`, `FAILED` |
| `RUNNING` | `PAUSE_REQUESTED`, `CANCEL_REQUESTED`, `COMPLETED`, `FAILED`, `RECOVERING` |
| `PAUSE_REQUESTED` | `PAUSED`, `COMPLETED`, `FAILED`, `RECOVERING` |
| `PAUSED` | `PENDING`, `CANCEL_REQUESTED` |
| `CANCEL_REQUESTED` | `CANCELLED`, `COMPLETED`, `FAILED`, `RECOVERING` |
| `RECOVERING` | `CLAIMED`, `RUNNING`, `PAUSED`, `CANCELLED`, `COMPLETED`, `FAILED` |
| `CANCELLED` | none |
| `COMPLETED` | none |
| `FAILED` | none |

Resume is the `PAUSED → PENDING` transition and permits a fresh claim. Pause
and cancel requests never directly assert that local execution stopped. A
request racing with a durable final commit may transition to `COMPLETED`,
because a committed dataset result cannot be undone by a late control request.
Terminal states are immutable.

The worker observes control requests before execution, between chunks, before
each new source request and after every local commit. It never interrupts a
Parquet/catalog journal transaction. The transition implementation must be
atomic and tested in PostgreSQL; application validation is additional defense,
not its sole authority.

### Reconciliation and recovery

An expired lease on `CLAIMED`, `RUNNING`, `PAUSE_REQUESTED` or
`CANCEL_REQUESTED` is moved to `RECOVERING` before reassignment. Recovery first
validates local journals and checkpoints under the existing dataset lease:

- a durable local commit is required before publishing `COMPLETED`;
- a `COMMITTED` journal remains successful if cleanup later fails;
- a confirmed chunk receipt advances progress without another fetch;
- a `PREPARED` journal follows the existing rollback rules;
- PostgreSQL progress behind verified local state is advanced;
- an unresolved contradiction becomes `FAILED` with a stable sanitized code;
- diagnostic artifacts are retained unless existing validated recovery rules
  explicitly identify them as safe cleanup.

Recovery is idempotent. Repeating it after any interruption must converge to
the same state without duplicating a request, chunk or local commit.

### API and frontend boundary

Phase 2D reserves authenticated administrator routes for planning/submitting
RAW operations, inspecting operations/datasets and requesting cooperative
lifecycle changes. Dataset HTTP identifiers are canonical opaque encodings of
validated identity; they are never filesystem paths.

The minimal frontend requests a plan, displays bounded estimates, requires
explicit confirmation, submits with an idempotency key and polls sanitized
operation state. It never calculates plans, edits paths/manifests or talks
directly to PostgreSQL, Binance or local storage.

All routes require both a valid Supabase JWT and membership in
`public.app_admins`. The operational PostgreSQL tables have RLS enabled and no
Data API privileges. Only the backend's direct PostgreSQL connection may write
them. Logs contain operation/request IDs, canonical dataset identity, status,
bounded counts and stable error codes, never secrets, candle rows, payloads or
connection details.

### Deliberate limits

Phase 2D covers RAW backfill and incremental synchronization only. DERIVED
materialization, snapshots, periodic scheduling, multiple workers per volume,
distributed filesystems, cross-host coordination, non-crypto calendars,
strategies, indicators and backtests remain outside this boundary.

Operational rows and events have a documented 30-day retention target, but
Phase 2D performs no automatic cleanup. The architecture decision is recorded
in
[`docs/adr/0001-phase-2d-operational-market-data-control-plane.md`](./adr/0001-phase-2d-operational-market-data-control-plane.md).

## Phase 3A deterministic backtesting boundary

Phase 3A consumes immutable Phase 2C snapshots and publishes local results under
`ADT_DATA_DIR/market/backtests`. The backtest engine is independent from the
FastAPI request lifecycle, Supabase, Binance and the paper-simulation ledger.
It supports one crypto Spot instrument, long-only accounting and one strategy
per run.

The strategy boundary exposes immutable portfolio/order snapshots and a bounded
history containing only candles already processed. Orders returned from
`on_candle(T)` become eligible at `T+1`; the engine never exposes the
`MarketDatasetReader` or a future iterator. Snapshot identity and checksums are
validated before execution and revalidated after the final candle.

Financial state is derived with `Decimal`. Fills feed both the average-cost Spot
portfolio and an append-only SHA-256-chained local ledger. Risk validation is
outside strategy code. A drawdown halt cancels open orders, blocks new intents
and continues close-based marking.

Results are staged, fsynced and atomically promoted by deterministic `run_id`.
The manifest records input identity, execution assumptions, counts and artifact
checksums. Independent verification reconstructs the ledger, portfolio, trades,
equity and metrics without running the strategy again. The detailed lifecycle,
CLI and limitations are documented in
[`docs/BACKTESTING.md`](./BACKTESTING.md).

## Phase 4-01 deterministic parameter-search boundary

Phase 4-01 introduces `app/optimization` as a pure backend domain layer above
the Phase 3C strategy registry:

```text
explicit finite scalar values + fixed parameters
    → StrategyPluginDescriptor type/range normalization
    → canonical parameter/value order + bounded cardinality
    → StrategyPluginRegistry.build for every complete combination
    → immutable search space and planner-ready combinations
```

The layer does not invoke the backtest engine, access `ADT_DATA_DIR`, mutate
snapshots or datasets, use PostgreSQL, expose HTTP routes, or retain critical
state in memory. It creates only immutable in-process contracts and fresh
JSON-compatible projections.

Search-space schema version 1 binds plugin name/version/schema/lifecycle,
explicit fixed and searchable parameters, typed canonical values,
`REJECT_SPACE`, cardinality and the requested limit. Canonical JSON uses sorted
keys and compact ASCII encoding. Decimal text is derived from the exact sign,
digits and exponent tuple under a bounded 128-character persistence contract,
so results do not depend on global Decimal precision or rounding. Its final
length is calculated before coefficient text or zero padding is constructed;
extreme positive or negative exponents therefore fail without allocation
proportional to the exponent. Integers are limited to 128 magnitude digits and
are compared against exact powers of ten before decimal conversion, remaining
independent of Python's configurable integer-to-string limit.

The payload checksum is ordinary SHA-256. Search-space and combination IDs use
domain-separated SHA-256 namespaces; a combination binds the space ID, its
zero-based deterministic index and the existing Phase 3C parameter-document
checksum. IDs contain no clock, timezone, locale, hash iteration, UUID or random
state.

The service calculates Cartesian cardinality before constructing a combination,
defaults to 1,000 combinations and enforces an absolute ceiling of 100,000.
There is no truncation. The initial strict policy rejects the complete space at
the first combination refused by the real registered factory. Frozen public
contracts also reject invalid scalar kinds, non-canonical value order, duplicate
or overlapping names, unsupported schemas, invalid limits and a cardinality
different from the exact dimension product when constructed directly. As
defense in depth, `expand()` repeats these structural, schema, limit,
cardinality, checksum and ID validations before resolving or calling a strategy
factory. Experiment persistence/planning/execution, walk-forward and
overfitting analysis remain later Phase 4 deliveries.

## Phase 4-02 deterministic temporal-segmentation boundary

Phase 4-02 extends `app/optimization` with a second pure domain layer. It
accepts the existing Phase 2C `DatasetSnapshot` and `DatasetManifest` contracts
plus one explicit selected `DataRange`; it performs no snapshot I/O:

```text
immutable STRICT snapshot + manifest + selected [start, end)
    + train/validation/test candle counts + one warmup count
    → exact timeframe-slot arithmetic
    → TRAIN → VALIDATION → TEST contiguous evaluation ranges
    → immutable canonical plan and segment documents
```

Schema version 1 supports only `CONTIGUOUS_THREE_WAY`. All boundaries are
explicit UTC and use half-open intervals. The three positive integer counts
must consume the selected coverage exactly, without gaps, overlaps, embargo or
implicit remainder. Timeframe resolution and alignment use the Phase 2A
registry. The initial layer accepts only `STRICT` derived snapshots because a
gap-tolerant range cannot prove its actual candle count from duration alone.

One non-negative `warmup_candles` value applies to all segments. A segment is
scored only over `[evaluation_start, evaluation_end)`, while its read context is
`[evaluation_start - warmup, evaluation_end)`. Context is retrospective, may
cross an earlier evaluation boundary, never changes scored membership and must
remain inside the snapshot's available coverage. There is no strategy-based
warmup inference and no candle materialization.

The minimal snapshot reference binds snapshot ID/checksum, dataset key/version,
the existing dataset identity (exchange, market type, symbol, timeframe and
construction metadata), strict gap policy, available coverage and selected
coverage. Canonical JSON uses the Phase 4-01 codec. Ordinary SHA-256 covers
segment and final plan payloads; domain-separated SHA-256 identifies the plan
and binds each segment ID to that plan. Frozen public contracts validate direct
construction, and service/document boundaries repeat temporal, structural,
snapshot, checksum and ID validation to detect low-level mutation.

Snapshot authentication is not reimplemented in the optimization package.
Snapshot creation, `MarketDatasetReader` and temporal segmentation share the
pure Phase 2C helpers `build_snapshot_id()` and `validate_snapshot_contract()`.
Consequently, a snapshot is accepted only when its deterministic ID, fixed
manifest path, dataset identity/version/checksum, COMPLETE manifest, coverage
and exact canonically ordered partition projection agree. Malformed contracts
are checked by type before attribute access or timeframe lookup and are exposed
to the temporal boundary as stable domain failures.

Segment role and index form one invariant rather than independent fields:
`0/TRAIN`, `1/VALIDATION` and `2/TEST`. Direct construction and later service
revalidation reject every other association even if an attacker recalculates
the segment checksum and ID.

This delivery does not expand parameter spaces, execute a backtest, publish an
artifact, persist an experiment or implement walk-forward. Phase 4-03 is the
first layer allowed to join parameter search, temporal segmentation and a
backtest configuration into a reproducible experiment plan.

## Phase 4-03 reproducible experiment-planning boundary

Phase 4-03 is a pure composition layer above the existing contracts:

```text
legitimate Phase 2C snapshot + manifest
    + validated Phase 4-02 TRAIN / VALIDATION / TEST plan
    + validated and factory-expanded Phase 4-01 parameter space
    + exact registered Phase 3C plugin identity
    + canonical projection of common Phase 3A BacktestConfig fields
    → immutable experiment manifest
    → one planned run spec per combination × temporal segment
```

The planner reuses Phase 2C snapshot validation, the temporal service and the
parameter-search service. Snapshot/temporal integrity, plugin and search-space
structure, cardinality and the common backtest configuration are checked before
parameter expansion can invoke a strategy factory. There are exactly three runs
per combination, a default limit of 3,000 planned runs and a conservative
absolute limit of 30,000. No truncation or partial plan is possible. Canonical
ordering is `combination_index` first and `segment_index` second.

The experiment-wide backtest projection contains only common deterministic
fields. Every planned spec contains an actual existing `BacktestConfig` bound
to the immutable snapshot, normalized strategy descriptor and segment context
range. The separate evaluation range remains explicit: retrospective warmup is
read context, never scored membership. Phase 4-04 must preserve that distinction
when it adds execution.

`engine_version` is rejected unless it is already an exact safe token without
leading or trailing whitespace. The backtest schema is an integer (never a
boolean) from the official Phase 3A supported-version set, currently versions 1
and 2. Phase 4-03 reuses the Phase 4-01 pure combination validator for nested
parameter tuple, typed document, checksum and search-space-bound ID invariants.

Run purposes are inseparable from temporal roles: TRAIN is `TRAINING`,
VALIDATION is `MODEL_SELECTION`, and TEST is `FINAL_HOLDOUT`. Only VALIDATION is
marked eligible for future selection. The manifest records the strict
`TEST_IS_FINAL_HOLDOUT` policy; Phase 4-03 contains no ranking or selection.

Schema version 1 embeds the complete canonical 4-01 and 4-02 documents,
combination parameter documents, plugin versions, backtest settings, ordered
specs, limits and policies. Each documented run is compact: it references its
top-level combination by index, ID and parameter checksum and its temporal
segment by index, ID and checksum; context, evaluation, snapshot, plugin and
backtest config are reconstructed from top-level contracts. Ordinary SHA-256
protects payloads. Domain-separated
SHA-256 identifies experiments and planned specs; planned-spec IDs bind their
experiment and are deliberately distinct from Phase 3A completed-result
`run_id` values. Frozen contracts, strict decoding and consuming-service
revalidation cover low-level mutation, ordering, cardinality, holdout, checksum
and identity drift.

This boundary reads no candles, calls no backtest engine, creates no result
artifact or Phase 3A run ID, performs no filesystem/database/network write and
starts no worker, thread or subprocess. Bounded execution and atomic result
publication belong exclusively to Phase 4-04.
