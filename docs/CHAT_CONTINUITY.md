# ADT Current Development Handoff

Last updated: 2026-08-21

## Current branch

`feat/phase-7-06-operational-mandate-foundation`

At the start of every session, verify the local branch and HEAD, then inspect
the corresponding remote branch. This file records the intended handoff; Git
remains the evidence of the repository's actual state.

## Current phase

**Phase 7 — Operational Control Plane — ACTIVE**

Phase 6 is complete and versioned. Phase 7 remains active and is not complete.

## Last completed track

**7-05 — Worker Runtime Observability — CLOSED**

Gate 5 — Delivery-wide Closure Audit passed and closed the implementation. The
accepted persistence, repository, runtime, HTTP and frontend gates, final full
backend/frontend/browser regressions and delivery-wide safety audit support the
closure.

Previously closed Phase 7 deliveries remain closed:

- **7-04 — RAW Gap & Quality Inspection — CLOSED**
- **7-03 — Persisted RAW Dataset Inspection — CLOSED**
- **7-02 — Market Operation Administrative Console — CLOSED**
- **7-01 — Control Plane Foundation — CLOSED**
- **7-01D2C1 — Expired Operation Recovery — CLOSED**
- **7-01D2C2 — Pre-claim Control Settlement — CLOSED**
- **7-01D2C3 — Structured Cancellation & Graceful Shutdown — CLOSED**

## Last validated implementation milestone

`0c53c96c9d2500b01d0dfab0e1dd74531e1e8d9c`

## Current integrated main milestone

`d912b44867f111c73759077f86c072eb2a0c2542`

## Active delivery

**7-06 — Operational Mandate Foundation — ACTIVE / BOOTSTRAP**

Starting main baseline:

`d912b44867f111c73759077f86c072eb2a0c2542`

The next-delivery selection audit passed. Mandates were selected because they
are the missing authorization authority for later paper-session configuration,
policy selection and durable runtime control.

Frozen boundary:

- create a durable, authenticated and auditable administrator-approved mandate
  authority for a bounded set of canonical instruments;
- derive asset and market projections from authorized instruments instead of
  maintaining independent authorization lists;
- identify authorization by exchange, market type and canonical trading pair;
- exclude mutable adapter metadata such as native symbol, active flag and
  precision from mandate authority;
- reject unsupported exchange/market combinations and preserve Binance Spot as
  the only currently supported operational boundary;
- use the one-way `DRAFT -> APPROVED -> ARCHIVED` lifecycle;
- persist immutable specification revisions, append a revision when editing a
  draft and use `expected_revision` for concurrent updates;
- atomically approve one exact revision and specification checksum; approved
  mandates cannot be edited in place;
- record auditable administrator actors and timestamps; one administrator may
  create and approve in this delivery;
- perform approval without Binance/network access or live-availability checks;
- allow future consumers to bind to `mandate_id + approved_revision +
  specification_checksum`;
- expose only bounded administrator APIs and a protected administrative
  frontend for listing, drafting, reviewing, approving and archiving;
- keep backend validation and transition legality authoritative;
- use PostgreSQL as mandate authority with RLS enabled and Data API privileges
  revoked;
- expose no hostname, PID, filesystem path, `ADT_DATA_DIR`, credentials or
  storage identifiers, and perform no long-running work inside HTTP.

Explicitly out of scope are timeframes and trading horizons; strategies,
sizing, fees, slippage and risk policies; initial or official capital;
paper-session creation or configuration; collector, paper-runner or worker
lifecycle; market-data ingestion or repair; network execution from
administrative HTTP; live availability as an approval prerequisite;
dual-control approval; approved-mandate mutation or supersession graphs;
capital-ledger integration; ADT Official Portfolio; ADT Confidence Score; ML;
Telegram; SaaS; deployment expansion; and real-capital execution.

Migration expected: **YES**. It may be designed, reviewed and validated locally
in later gates, but remote application is prohibited until separately reviewed.
No 7-06 implementation files or implementation milestone exist yet.

## 7-02 closure evidence

- Code milestone:
  `b7b2e2f258fe788d7c97bb09875a709058f4eec5`
- Parent/bootstrap milestone:
  `c4acf97fba0c3a56e71bd22430bbeabfd167844a`
- Protected staged patch SHA256:
  `01e2a995bd883002f3ae36fad5bff9fb6e059664c681cd08ef8905c5b14ab7a6`
- 7-02 Semantic Gate 2 + Full Local: PASS
- Default asyncio/AnyIO path validated on the authoritative local Kali
  environment without forcing uvloop
- Full backend: 2406 passed, 1 skipped
- Backend coverage: 87%
- Full frontend Vitest: 207 passed
- Frontend lint, typecheck, E2E typecheck, production build, generated API
  consistency and bundle budget: PASS
- Full Playwright: 50 passed
- Phase 6 accessibility regression suite: 10 passed
- Previously timing-sensitive mobile navigation scenario: 10/10 deterministic
  stress repetitions passed after removing fixed-link-count and transition-timing
  assumptions from the test
- No migration or dependency changes
- No repository, worker, C1, C2 or C3 changes
- No remote Supabase mutation, real Binance OHLCV execution or real-capital work
- No unresolved 7-02 blocker

## 7-01 closure evidence

- Final 7-01 Closure Audit: PASS
- Targeted closure suite: 520 passed
- Full backend: 2402 passed, 1 skipped
- Coverage: 87%
- Branch at audit: `33314bec2ff3f87b6af23fd52e553fcc3e2481c4`
- No unresolved 7-01 blocker
- Remote migration application remains operational/deployment work and is not
  a local code-closure blocker

## Delivered 7-01 boundary

- Authenticated administrative operation API and application service
- Durable PostgreSQL operational coordination
- Separate worker, runtime and CLI
- Normal execution with heartbeat, checkpoint and receipt settlement
- C1 restart recovery
- C2 pre-claim controls
- C3 graceful shutdown
- No long-running work inside FastAPI HTTP requests

## Explicitly not closed by 7-01

- Phase 7 as a whole
- The full historical Phase 2D scope
- Minimal administrative frontend
- Dataset, gap and quality administrative inspection
- Future broader operational mandates and session configuration
- ADT Official Portfolio
- ADT Confidence Score
- Telegram and SaaS
- Machine learning
- Production deployment
- Automated real-capital trading

## 7-03 closure evidence

- Starting baseline:
  `673b5d8c233b1f232e85636e9e03ff4bbe62395f`
- Bootstrap milestone:
  `4b39a0d6fe00709c1973e702fec23f9dbdd9d428`
- Backend implementation milestone:
  `ad8e5c08d940e75baea1cc57f8595bd5f58a7d8e`
- Frontend implementation milestone:
  `1445c074e5c02aebb6ef82cbf74acda8df06b286`
- Frontend protected staged patch SHA256:
  `50d1df06190dd6d3e361a9613f37df157af02564c10f97edb966d69dfced23bb`
- Remote branch verified at the same frontend implementation milestone
- Branch remained exactly 3 commits ahead and 0 behind starting `main`
- Backend Ruff: PASS
- Backend format: PASS
- Backend MyPy strict: PASS in 211 source files
- Full backend: 2432 passed, 1 skipped
- Backend coverage: 87%
- Backend pip check: PASS
- Frontend OpenAPI freshness: PASS
- Frontend typecheck and lint: PASS
- Full frontend Vitest: 28 files, 214 passed
- Production build and bundle budget: PASS
- Frontend E2E typecheck: PASS
- Full Playwright: 50 passed
- No migration or dependency changes
- No RAW mutation method was added to the frontend
- No `ADT_DATA_DIR`, filesystem path or manifest `relative_path` is exposed
  through the browser/API contract
- Internal manifest `relative_path` remains used only for backend integrity
  verification
- No gap scan, quality scan, repair, ingestion or Binance/network execution
  occurs in the inspection HTTP path
- Working tree clean after implementation checkpoints
- No unresolved 7-03 blocker

## 7-04 closure evidence

**7-04 — RAW Gap & Quality Inspection — CLOSED**

Starting baseline:

`8dc3fcc8a524f969f94526ecc1b3c017a6f53900`

Implementation milestones:

- Bootstrap:
  `7665d2921474af02ae98a61ab98fbb7509501ce5`
- Gate 1A — bounded shared RAW dataset snapshot locking:
  `7f67f6853533be5fe071af2d0fdf523f4b58df96`
- Gate 1B — bounded deterministic RAW gap inspection:
  `9372cf8815d8c1ff8701f96271381a068ad9a8c0`
- Gate 1C — persisted RAW quality-baseline inspection:
  `8f9bad3f9198f857b58ccd3466f0b58ff54047a7`
- Gate 2 — authenticated HTTP schemas, routes and application wiring:
  `8af75a7e9bee732b91728b6d65f04c83742ee1e3`
- Gate 3 — generated OpenAPI client and protected RAW administration UI:
  `f155d5a1dc1dfddbca73f1125883c93088a9680d`

Protected patch evidence:

- Gate 2 staged patch SHA256:
  `58fe0b01a1146f66a2b82db5d607c478293f4207488d3403e6cacfcc167647be`
- Gate 3 staged patch SHA256:
  `2656320f25b4b324c8d459bfe9c7e13719d1e0f6b36c602e88bdc6f0a5d87a01`

Closure evidence:

- Final Ruff check: PASS
- Final Ruff format check: PASS, 330 files already formatted
- Final MyPy strict: PASS in 213 source files
- Full backend: 2469 passed, 1 skipped
- Backend coverage: 87%
- Backend pip check: PASS
- Full frontend Vitest: 28 files, 219 passed
- Frontend typecheck: PASS
- Frontend lint: PASS
- Generated OpenAPI freshness: PASS
- Production build: PASS
- Frontend E2E typecheck: PASS
- Bundle budget: PASS
- Full Playwright: 50 passed
- No migration or dependency changes
- No skip or xfail was added to manufacture a passing gate
- RAW gap inspection is limited to 10,000 expected candles per HTTP request
  and page size at most 100
- Gap inspection reuses the deterministic Phase 2B missing-candle semantics
- Snapshot readers use bounded non-blocking shared `flock` acquisition while
  existing writers retain exclusive locking
- Snapshot readers do not rewrite writer lock metadata or invoke recovery
- Persisted RAW quality inspection reads and validates the existing
  `FULL_DATASET` baseline without executing a scanner
- Quality status is sanitized to `CURRENT`, `STALE`, `MISSING` or `INVALID`
- Quality issue samples expose only `code`, `severity`, `category` and
  `open_time`
- Administrative gap and quality endpoints are administrator-only GET routes
  with `Cache-Control: no-store`
- No recovery, repair, backfill, operation submission, quality scan, network
  access or durable dataset mutation occurs in the new inspection GET paths
- No `ADT_DATA_DIR`, baseline path, partition path, manifest `relative_path`
  or partition checksum is exposed by the new browser/API contract
- The frontend extends the existing protected RAW dataset detail surface and
  does not create a second dataset authority
- No scan, repair, backfill or mutation action was added to the RAW inspection
  UI
- Final delivery audit contained exactly 21 files relative to the starting
  `main`
- At implementation checkpoint
  `f155d5a1dc1dfddbca73f1125883c93088a9680d`, the feature was 6 commits ahead
  and 0 behind the starting `main`
- Working tree was clean at implementation closure
- No unresolved 7-04 implementation blocker remains

Phase 7 itself remains **ACTIVE**. Closing 7-04 does not close or tag Phase 7.

## Last completed delivery

**7-05 — Worker Runtime Observability — CLOSED**

Starting baseline:

`f0f606e9f7d302c85d7b7a621604f67fc84676a9`

This is the starting integrated `main` baseline for 7-05, not an implementation
milestone.

Implementation milestones:

- Bootstrap:
  `c288cd6b44fb36625b506cec5bc9a3b8d9c248af`
- Persistence foundation:
  `70367174f3d4afc69e1e9bc1b90a157a25b113ef`
- Repository/domain contract:
  `1367d56040ab3ed2414aaaa2b25db03df320db68`
- Persistent runtime wiring:
  `8346195fd08c607c97ec05f9e5e24d06eaf8b4e1`
- Administrative observability API:
  `ecca557b51b02fdcac28920b32a15fe755a85882`
- Administrative observability frontend:
  `0c53c96c9d2500b01d0dfab0e1dd74531e1e8d9c`

Protected patch evidence:

- Gate 2A persistence patch SHA256:
  `ba19c1769e4563df0054a2d02d2891b9611df11e8f69f727e0b194038d70e149`
- Gate 2B repository patch SHA256:
  `a877b7db631e2b0a2abe0b96eb6b58a2b0dfeece0a699a5a659aeb8ab4a50289`
- Gate 2C runtime wiring patch SHA256:
  `779368015f23a787d8409f821e9d8f42cca199c402c9be7afd95ed5fb0c1bd5f`
- Gate 3 API patch SHA256:
  `52c7834a0bf3b237e24f0431193fa0d29e56608724b0b8d149051f7b6bbae5e1`
- Gate 4 frontend patch SHA256:
  `f87fdfebd669e9214420f4ae5cfd3d98d6ba3a07d7ed99ec38ddf2c72f46e181`

Closure evidence:

- Gate 5 — Delivery-wide Closure Audit: PASS / CLOSED
- Ruff check: PASS
- Ruff format: PASS, 345 files already formatted
- MyPy strict: PASS in 220 source files
- Full backend: 2579 passed, 1 skipped
- Backend coverage: 87%
- pip check: PASS
- Targeted 7-05 closure: 110 passed
- Full frontend Vitest: 29 files, 225 passed
- Frontend typecheck, E2E typecheck, ESLint, generated OpenAPI freshness and
  production build: PASS
- Full Playwright: 53 passed
- Exactly two authenticated administrator-only GET observability routes:
  `/runtimes` and `/events`
- HTTP/API reads are bounded, sanitized and use `Cache-Control: no-store`
- The migration remained unchanged after persistence Gate 2A
- RLS and Data API privilege revokes were reviewed for both observability tables
- The migration was not applied remotely
- Final delivery audit contained 37 files relative to the starting `main`
- The implementation contains 6 commits relative to that baseline
- The remote branch was verified at the final implementation milestone
- The working tree was clean at implementation closure
- No unresolved 7-05 implementation blocker remains

Post-integration evidence:

- Documentation closure commit:
  `8a9c6eaaadf8e2fd07f56c69ae497d5b3563c003`
- 7-05 was integrated into `main` by fast-forward
- `origin/main` and the 7-05 feature branch were verified identical at
  `8a9c6eaaadf8e2fd07f56c69ae497d5b3563c003`
- No merge commit was created

Delivered boundary:

- PostgreSQL-backed worker runtime presence and bounded operational events
- Domain contracts, ports, repository and persistent runtime wiring
- Independent heartbeat while the worker is `IDLE` or `ACTIVE`
- `HEALTHY`, `STALE`, `STOPPED` and `FAILED` health projections
- Generated OpenAPI/client contracts and a protected read-only
  `/admin/worker-observability` frontend
- Bounded 30-second polling with runtime limit 20 and event limit 50
- React StrictMode-safe browser regression coverage

Contract and safety boundaries preserved:

- worker runtime presence is distinct from an operation lease;
- an expired heartbeat is not proof that a prior process has terminated;
- HTTP performs no long-running worker work;
- HTTP exposes no internal runtime UUID, hostname, PID, filesystem path,
  `ADT_DATA_DIR`, credentials or storage identifiers;
- operational events are bounded and sanitized;
- no worker lifecycle mutation is exposed through HTTP;
- observability is not worker fencing or takeover authority;
- one worker per persistent volume remains a deployment contract;
- existing market-data operation, local durability and recovery contracts remain
  authoritative.

Migration status:

`supabase/migrations/20260819000000_phase_7_05_worker_runtime_observability.sql`
was reviewed with RLS enabled and Data API privileges revoked, but it was not
applied remotely. Remote application remains a separate operational/deployment
step.

Explicitly deferred after 7-05:

- worker lifecycle mutation from HTTP;
- collectors and paper runners;
- mandates and paper-session configuration;
- capital-ledger integration and Official Portfolio;
- machine learning, Telegram, SaaS, deployment and real-capital execution.

Phase 7 remains **ACTIVE** and has no Phase 7 tag. Delivery 7-06 is selected and
bootstrapped on its frozen documentation boundary; the remaining items in
[`ROADMAP.md`](./ROADMAP.md) continue to govern later scope.

## Important repository and process constraints

- Repository root: `~/programaçao/ADT`
- The root `.env` is local and secret; never commit or print it.
- Never perform a destructive reset of a linked Supabase project.
- Never push a remote migration without explicit review and authorization.
- Long-running work must never execute inside a FastAPI HTTP request.
- Preserve deterministic, resumable, idempotent and auditable contracts.
- Preserve strict typing, tests, Ruff and format gates.
- Real-capital trading remains outside the active implementation scope.
- Preserve existing CLI compatibility unless a delivery explicitly changes it.
- Do not let obsolete phase numbering in `ADT_SPEC.md` override
  `PRODUCT_VISION.md`, `ROADMAP.md` or this handoff.
- Verify branch, HEAD, staging, untracked files and remote state before work.
- Stop when a required gate fails; never manufacture PASS with skip, xfail,
  ignore or selective exclusion.
- Under zsh, do not use `path` as a shell loop variable because it aliases and
  synchronizes with `PATH`; prefer `file_path` or `file_name`.

## Product alignment summary

- Administrator: orchestrator of approved mandates, not a manual signal caller.
- ADT Official Portfolio: future canonical public paper-performance portfolio.
- ADT Confidence Score: evidence strength, not financial return or an automatic
  probability of profit.
- Public visitor: spectator with safe read-only projections.
- Subscriber/SaaS: future premium-signal and entitlement direction.
- Telegram: future distribution with human manual execution initially.
- ML/intelligence: Phase 8 and later, under explicit evaluation gates.
- Automated real-capital execution: optional separate future assessment.

See [`PRODUCT_VISION.md`](./PRODUCT_VISION.md) for the durable product contract
instead of expanding this current handoff into a duplicate product history.
