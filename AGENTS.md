# ADT Project Guide for AI Assistants

## Purpose

This file instructs AI assistants (like GitHub Copilot, Claude, or ChatGPT) on how to work effectively within the ADT project.

## Before You Start

**Always read in this order:**

1. [docs/PRODUCT_VISION.md](./docs/PRODUCT_VISION.md) — Durable product direction
2. [docs/CHAT_CONTINUITY.md](./docs/CHAT_CONTINUITY.md) — Current branch, phase and handoff
3. [docs/ROADMAP.md](./docs/ROADMAP.md) — Current authoritative phase plan
4. [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) — Technical design and boundaries
5. Phase-specific documents referenced by the current handoff

[docs/ADT_SPEC.md](./docs/ADT_SPEC.md) remains useful as foundational and
historical specification context. Its obsolete phase numbering must not
override `PRODUCT_VISION.md`, `CHAT_CONTINUITY.md` or `ROADMAP.md`.

**Never assume** — Check documentation and repository evidence first.

## Session-Start Protocol

Before proposing implementation:

1. Read the authoritative files above.
2. Inspect the current local branch, HEAD, worktree and remote branch state.
3. Reconcile documentation claims with Git and repository evidence.
4. Report where the project stopped and which delivery is next.
5. Only then continue with an approved scope.

If `CHAT_CONTINUITY.md` and actual Git state differ, do not guess. Report the
discrepancy, use repository evidence to determine the actual state, and decide
whether the handoff needs a controlled update.

## Project Rules (Non-Negotiable)

### Security

- ❌ **Never commit `.env` files or secrets**
- ❌ **Never expose Supabase secret key in frontend**
- ❌ **Never hardcode API keys, tokens, or credentials**
- ✅ Use `VITE_` prefix for frontend environment variables (public)
- ✅ Use `ADT_` prefix for backend environment variables
- ✅ Document all required env vars in `.env.example`

### Architecture

- ❌ **Never put financial logic in the frontend**
- ❌ **Never hardcode market data, indicators, or strategies** in core code
- ❌ **Never use CSV as the primary data store**
- ❌ **Never create fixed local file paths** — use `ADT_DATA_DIR` config
- ✅ Frontend is a UI layer only
- ✅ Backend handles all calculations
- ✅ Workers handle async jobs
- ✅ Strategies are plugins, not core code
- ✅ All market-specific logic in adapters

### Development

- ❌ **Never install dependencies globally**
- ❌ **Never skip tests** for financial calculations
- ❌ **Never use real credentials** for development/testing
- ❌ **Never operate on real capital** — always paper trade
- ✅ Run tests locally before suggesting code
- ✅ Add type hints to Python code (required)
- ✅ Use TypeScript (no `.js` or untyped code in frontend)
- ✅ Follow existing code patterns

### Phases

Current Phase: **Phase 6 complete; Phase 7 active**

Read the exact current branch, completed delivery and next delivery from
[`docs/CHAT_CONTINUITY.md`](./docs/CHAT_CONTINUITY.md). Do not hardcode a commit
SHA or task handoff in this universal guide.

- ✅ Phase 4 is available as a stable boundary: finite deterministic search,
  temporal planning, bounded execution, walk-forward selection and explicit
  out-of-sample stability controls
- ✅ Phase 5 is the stable deterministic paper-trading boundary
- ✅ Phase 6-01 through 6-07 are complete
- ✅ The full local backend/frontend/browser gate, staged-diff audit and
  fast-forward integration into `main` passed
- ✅ The Phase 6 release is versioned by the annotated `phase-6` tag
- ❌ Cannot work on: unbounded/random search, genetic or Bayesian optimization,
  real-capital orders, leverage, shorts, derivatives, distributed execution or
  machine learning

Phase 6 remains a read-only presentation and analysis boundary. Operational
session/worker controls are active Phase 7 work, and real-capital trading
remains outside the current implementation scope. The formerly named Phase 2D
operational-administration scope continues inside the Phase 7 control-plane
track.

See [docs/BACKTESTING.md](./docs/BACKTESTING.md) and
[docs/ROADMAP.md](./docs/ROADMAP.md).

## Project Structure

Quick reference:
