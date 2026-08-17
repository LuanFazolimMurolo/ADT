# ADT Current Development Handoff

Last updated: 2026-08-16

## Current branch

`feat/phase-7-04-raw-gap-quality-inspection`

At the start of every session, verify the local branch and HEAD, then inspect
the corresponding remote branch. This file records the intended handoff; Git
remains the evidence of the repository's actual state.

## Current phase

**Phase 7 — Operational Control Plane — ACTIVE**

Phase 6 is complete and versioned. Phase 7 remains active and is not complete.

## Last completed track

**7-03 — Persisted RAW Dataset Inspection — CLOSED**

Formal closure is supported by the accepted backend and frontend gates,
independent remote verification, full repository regressions and the final
structural/safety closure audit.

The previous control-plane foundation delivery remains closed:

- **7-01 — Control Plane Foundation — CLOSED**
- **7-01D2C1 — Expired Operation Recovery — CLOSED**
- **7-01D2C2 — Pre-claim Control Settlement — CLOSED**
- **7-01D2C3 — Structured Cancellation & Graceful Shutdown — CLOSED**

## Last validated code milestone

`1445c074e5c02aebb6ef82cbf74acda8df06b286`

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

## Current delivery

**7-04 — RAW Gap & Quality Inspection — ACTIVE**

Starting baseline:
`8dc3fcc8a524f969f94526ecc1b3c017a6f53900`.

No 7-04 production implementation has been committed yet.

**Approved objective**: Extend the authenticated RAW dataset administration
surface with bounded read-only gap inspection and persisted quality-baseline
inspection while preserving the 7-03 storage-sanitization and HTTP-latency
boundaries.

**Administrative API contract**:

- `GET /api/v1/admin/market-data/datasets/{dataset_id}/gaps`
- `GET /api/v1/admin/market-data/datasets/{dataset_id}/quality`
- both endpoints use only the backend-owned canonical `dataset_id`;
- both endpoints are authenticated administrator-only and `no-store`;
- neither endpoint submits operations or mutates durable dataset state.

**RAW gap inspection contract**:

- explicit UTC half-open `start` and `end` are required;
- both boundaries must be aligned to the dataset timeframe;
- the requested interval must remain inside the persisted dataset coverage;
- at most 10,000 expected candles may be inspected per HTTP request;
- gap ranges use the existing deterministic Phase 2B missing-candle semantics;
- results are canonically ordered and paginated with page size at most 100;
- responses expose checked range, expected/observed/missing counts,
  total gap count, dataset version and bounded gap start/end ranges;
- HTTP gap inspection must not create a repair plan, job id or operational job;
- no recovery hook may execute as part of the GET request.

**Read-snapshot locking contract**:

- add a bounded shared dataset snapshot lock using the same kernel flock file
  as the existing exclusive writer lock;
- readers use non-blocking `LOCK_SH` acquisition with a monotonic deadline;
- existing writers retain `LOCK_EX` semantics unchanged;
- a snapshot reader never truncates or rewrites lock metadata;
- a snapshot reader never invokes transaction recovery;
- timeout produces only a sanitized service-unavailable response;
- dataset snapshot lock precedes any catalog snapshot lock so lock ordering
  remains deterministic.

**Persisted RAW quality contract**:

- HTTP never invokes `AdvancedMarketDataQualityScanner.scan`;
- HTTP reads only the deterministic persisted `FULL_DATASET` RAW quality
  baseline created by the existing local quality workflow;
- baseline envelope/checksum and semantic identity are validated before use;
- baseline dataset version and version algorithm are correlated with the
  current catalog snapshot;
- the sanitized quality status is one of `CURRENT`, `STALE`, `MISSING` or
  `INVALID`;
- missing, stale or invalid baselines are reported read-only and never trigger
  an automatic scan;
- responses may expose scanner schema/version, sanitized coverage counters,
  partition count, issue totals and a deterministic bounded issue sample;
- exposed issue fields are limited to `code`, `severity`, `category` and
  `open_time`;
- partition identifiers and baseline filesystem locations are never exposed.

**Frontend contract**:

- extend the existing protected RAW dataset detail surface instead of creating
  a second dataset authority;
- gaps use explicit start/end filters and bounded pagination;
- quality displays persisted-baseline status and sanitized summary;
- no repair, refresh-scan, backfill or mutation action is added;
- generated OpenAPI contracts and the typed API client remain authoritative.

**Expected migration**: NO.

**Explicitly out of scope**:

- running FULL or INCREMENTAL quality scans from HTTP;
- generating or replacing quality baselines from HTTP;
- RAW gap repair;
- RAW backfill or incremental submission;
- operation lifecycle controls already owned by 7-02;
- DERIVED dataset quality or snapshots;
- exposing `ADT_DATA_DIR`, filesystem paths, baseline paths, partition paths,
  manifest `relative_path` or partition checksums;
- Binance or other network access;
- PostgreSQL, Parquet or catalog mutation;
- worker-global control, collectors, paper runners or mandates;
- machine learning, Telegram, SaaS, deployment and real-capital execution.

**Gate requirement**: Any implementation that performs recovery, repair,
quality scanning, network access or durable dataset mutation inside either GET
endpoint is a semantic blocker and cannot pass 7-04.

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
