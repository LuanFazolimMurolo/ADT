# ADT Current Development Handoff

Last updated: 2026-08-15

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

**7-01D2C2 — Pre-claim Control Settlement — CLOSED**

**7-01D2C1 — Expired Operation Recovery — CLOSED** remains the protected
recovery foundation consumed by C2.

C2 atomically settles never-started Category A controls. Previously-started
Category B controls acquire settlement-only ownership and reconcile durable
local state. Both categories use one globally ordered C2 path, their crash
windows compose with C1 recovery, and no executor or fetch runs during C2
settlement.

Last validated **code milestone** commit:

`b7815996443286a6663a2e08903b15726c3d2e96`

This is not a permanent assertion about the feature branch HEAD. The
documentation continuity commit and later deliveries naturally make the branch
newer. Verify the current local and remote SHAs at session start.

## Accepted C2 evidence

- Gate 1: PASS
- Category B Gate 1 Retry: PASS
- Semantic Gate 2: PASS
- Full Local: PASS
- Backend suite: 2389 passed, 1 skipped, 87% coverage
- Protected patch SHA:
  `2a65c25a59dae81fa1aea42d4b8051c42ef81b2158f607c5517fba58e2a9af2a`
- Protected code commit:
  `b7815996443286a6663a2e08903b15726c3d2e96`

## Next technical delivery

**7-01D2C3 — Structured Cancellation & Graceful Shutdown**

Known objective: structured cooperative cancellation of active execution tasks,
with no orphan child tasks, graceful `SIGTERM`/drain in the continuous runtime,
and no stale settlement after cancellation or lease loss.

The exact implementation must be reconfirmed against the current repository,
state-machine contracts and tests before code changes begin.

## Remaining 7-01 closure

1. **7-01D2C3 — Structured Cancellation & Graceful Shutdown**
2. final **7-01 closure audit**
3. continue Phase 7 according to [`ROADMAP.md`](./ROADMAP.md)

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
