# ADT Current Development Handoff

Last updated: 2026-08-16

## Current branch

`feat/phase-7-03-raw-dataset-inspection`

At the start of every session, verify the local branch and HEAD, then inspect
the corresponding remote branch. This file records the intended handoff; Git
remains the evidence of the repository's actual state.

## Current phase

**Phase 7 — Operational Control Plane — ACTIVE**

Phase 6 is complete and versioned. Phase 7 remains active and is not complete.

## Last completed track

**7-02 — Market Operation Administrative Console — CLOSED**

Formal closure is supported by the accepted 7-02 Semantic Gate 2 + Full Local
validation and protected staged audit.

The previous control-plane foundation delivery remains closed:

- **7-01 — Control Plane Foundation — CLOSED**
- **7-01D2C1 — Expired Operation Recovery — CLOSED**
- **7-01D2C2 — Pre-claim Control Settlement — CLOSED**
- **7-01D2C3 — Structured Cancellation & Graceful Shutdown — CLOSED**

## Last validated code milestone

`b7b2e2f258fe788d7c97bb09875a709058f4eec5`

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

## Current delivery

**7-03 — Persisted RAW Dataset Inspection — ACTIVE**

Starting baseline:
`673b5d8c233b1f232e85636e9e03ff4bbe62395f`.

No 7-03 production implementation has been committed yet.

**Approved objective**: Provide authenticated administrators with a bounded,
read-only browser projection of locally cataloged RAW market-data datasets
without exposing filesystem layout and without executing scans, repairs,
ingestion or other long-running work inside HTTP requests.

**In scope**:

- list locally cataloged RAW datasets through a bounded administrative API;
- inspect one RAW dataset through its backend-owned canonical `dataset_id`;
- expose canonical exchange, market type, symbol and timeframe identity;
- expose persisted first/last open time, candle count, dataset version,
  version algorithm and catalog update time;
- expose only a sanitized integrity-manifest summary, such as presence/schema
  and partition count, without partition paths;
- deterministic ordering, bounded pagination and useful administrative filters;
- generated frontend API contracts and typed client methods;
- protected administrator navigation and a read-only dataset list/detail UI;
- accessibility, responsive behavior and deterministic local tests.

**Safety contract**:

- never expose `location`, filesystem paths or manifest `relative_path`;
- never expose local `ADT_DATA_DIR`;
- never perform Binance/network access for dataset inspection;
- never run gap discovery, quality scanning, repair or ingestion;
- never mutate Parquet, the local catalog or PostgreSQL;
- reuse backend-owned dataset identity encoding rather than reproducing it in
  the browser;
- retain the existing RAW catalog, transaction, locking and candle contracts as
  authoritative.

**Expected migration**: NO.

**Explicitly out of scope**:

- RAW gap discovery or repair;
- advanced quality scans or quality-baseline management;
- DERIVED datasets and snapshots;
- operation submission or lifecycle controls already owned by 7-02;
- worker-global health, presence and operational events;
- collector and paper-runner lifecycle control;
- administrator mandates and paper-session creation;
- capital-ledger integration and the ADT Official Portfolio;
- machine learning, Telegram, SaaS and deployment; and
- real-capital execution.

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
