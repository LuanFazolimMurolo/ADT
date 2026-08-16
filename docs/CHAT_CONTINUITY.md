# ADT Current Development Handoff

Last updated: 2026-08-16

## Current branch

`feat/phase-7-02-market-operation-admin-console`

At the start of every session, verify the local branch and HEAD, then inspect
the corresponding remote branch. This file records the intended handoff; Git
remains the evidence of the repository's actual state.

## Current phase

**Phase 7 — Operational Control Plane — ACTIVE**

Phase 6 is complete and versioned. Phase 7 remains active and is not complete.

## Last completed track

**7-01 — Control Plane Foundation — CLOSED**

Formal closure is supported by the accepted Final 7-01 Closure Audit.

The control-plane recovery and shutdown deliveries are also closed:

- **7-01D2C1 — Expired Operation Recovery — CLOSED**
- **7-01D2C2 — Pre-claim Control Settlement — CLOSED**
- **7-01D2C3 — Structured Cancellation & Graceful Shutdown — CLOSED**

## Last validated code milestone

`e4970a75af7478b1425e1cac45eba8e25c34a37b`

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

**7-02 — Market Operation Administrative Console — ACTIVE**

Starting baseline:
`d0ce8f25633103da3ae01e5e951ef0905ef4bd48`.

No 7-02 production implementation has been committed yet.

**Approved objective**: Provide an authenticated administrator browser console
for the existing 7-01 `MarketOperation` preview, submission, monitoring and
control boundary. FastAPI must not perform physical market-data execution.

**In scope**:

- backend-owned target resolution;
- explicit preview, confirmation and submission for RAW backfill and RAW
  incremental operations;
- operation list and detail views;
- bounded polling and progress, result, failure and timestamp presentation;
- safe lease-time presentation without asserting that a worker is online;
- pause, resume and cancel using the current `record_version`;
- stable idempotency keys for ambiguous retries of the same confirmed intent;
- generated frontend API contracts; and
- protected, accessible and responsive administrator navigation and UI.

**Out of scope**:

- dataset, gap and quality inspection or repair;
- worker-global health, presence and operational events;
- collector and paper-runner lifecycle control;
- administrator mandates and paper-session creation;
- capital-ledger integration and the ADT Official Portfolio;
- machine learning, Telegram, SaaS and deployment; and
- real-capital execution.

**Expected migration**: NO.

The existing 7-01 API, worker, C1 recovery, C2 pre-claim settlement, C3 graceful
shutdown, local `flock` and CLI contracts remain authoritative. Controls use
optimistic record versions and conflicts require reload/reconciliation rather
than blind mutation retries. The same confirmed intent retains its non-sensitive
idempotency key across an ambiguous retry; a new intent receives a new key.

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
