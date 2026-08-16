# ADT Current Development Handoff

Last updated: 2026-08-16

## Current branch

`feat/phase-7-01-control-plane-foundation`

At the start of every session, verify the local branch and HEAD, then inspect
the corresponding remote branch. This file records the intended handoff; Git
remains the evidence of the repository's actual state.

## Current phase

**Phase 7 — Operational Control Plane**

Phase 6 is complete and versioned. Phase 7 is active.

## Current track

**7-01 — Control Plane Foundation**

## Last completed delivery

**7-01D2C3 — Structured Cancellation & Graceful Shutdown — CLOSED**

The preceding control-plane recovery deliveries also remain closed:

- **7-01D2C1 — Expired Operation Recovery — CLOSED**
- **7-01D2C2 — Pre-claim Control Settlement — CLOSED**

C3 gives executor and heartbeat children structured cancellation and joining.
Parent cancellation propagates without stale PostgreSQL settlement, and the
continuous runner supports an explicit idempotent stop using Policy B
cooperative cancellation of an active `run_once`. `SIGTERM` and `SIGINT` route
to that contract at the runtime/process boundary, previous signal handlers are
restored after the owned lifecycle, and all asynchronous work joins before
runtime resources close. Shutdown composes with C1 recovery rather than
fabricating an administrative or terminal transition.

Last validated **code milestone** commit:

`e4970a75af7478b1425e1cac45eba8e25c34a37b`

This is not a permanent assertion about the feature branch HEAD. The
documentation continuity commit and later deliveries naturally make the branch
newer. Verify the current local and remote SHAs at session start.

## Accepted C3 evidence

- Gate 1: PASS
- Semantic Gate 2: PASS
- Full Local: PASS
- Backend suite: 2402 passed, 1 skipped, 87% coverage
- Protected patch SHA:
  `94e60940c97775e5acb5495a9b6036b50671dd558cc8fde1588c50b7abd90861`
- Protected code commit:
  `e4970a75af7478b1425e1cac45eba8e25c34a37b`

## Next task

**FINAL 7-01 CLOSURE AUDIT**

No new implementation delivery should start before this audit confirms whether
7-01 Control Plane Foundation is complete. C3 is closed; **7-01 is not yet
closed**.

The closure audit must:

- audit 7-01 as a whole;
- verify C1/C2/C3 composition;
- verify the original 7-01 scope and invariants;
- verify that no unresolved blocker remains;
- reconcile documentation with actual branch history;
- decide whether 7-01 can be formally closed; and
- only then select the next Phase 7 delivery from [`ROADMAP.md`](./ROADMAP.md).

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
