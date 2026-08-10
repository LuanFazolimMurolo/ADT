# ADT Project Guide for AI Assistants

## Purpose

This file instructs AI assistants (like GitHub Copilot, Claude, or ChatGPT) on how to work effectively within the ADT project.

## Before You Start

**Always read:**

1. [docs/ADT_SPEC.md](./docs/ADT_SPEC.md) — Product vision and constraints
2. [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) — Technical design and boundaries
3. [docs/ROADMAP.md](./docs/ROADMAP.md) — Planned phases

**Never assume** — Check documentation first.

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

Current Phase: **Phase 6 complete; Phase 7 has not started**

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
session/worker controls remain Phase 7 work, and real-capital trading remains
outside the current implementation scope. Phase 2D operational administration
continues as a separate control-plane track.

See [docs/BACKTESTING.md](./docs/BACKTESTING.md) and
[docs/ROADMAP.md](./docs/ROADMAP.md).

## Project Structure

Quick reference:
