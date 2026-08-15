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

**7-01D2C1 — Expired Operation Recovery — CLOSED**

- C1A — Atomic Recovery Claim & Ownership Hardening — COMPLETE
- C1B — Worker Recovery & Crash-Window Convergence — COMPLETE

Last validated **code milestone** commit:

`496da983f9f69b02151ddf51913e81d27c902c97`

This is not a permanent assertion about the feature branch HEAD. The
documentation continuity commit and later deliveries naturally make the branch
newer. Verify the current local and remote SHAs at session start.

## Accepted C1B evidence

- Gate 1: PASS
- Gate 2 Retry: PASS
- Full Local Gate Retry: PASS
- Backend suite: 2353 passed, 1 skipped, 87% coverage
- Staged Semantic Audit: PASS
- Protected patch SHA:
  `7ce9051a056c01d45416dccfea6325e258f240d8aab003b74b0df323bbdba125`
- Protected code commit:
  `496da983f9f69b02151ddf51913e81d27c902c97`

## Next technical delivery

**7-01D2C2 — Pre-claim Control Settlement**

Known objective: resolve administrative `PAUSE_REQUESTED` and
`CANCEL_REQUESTED` states that can occur before the first worker claim, without
fabricating execution ownership, `started_at` or local work.

The exact implementation must be reconfirmed against the current repository,
state-machine contracts and tests before code changes begin.

## Remaining D2 closure

After C2:

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
