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

Current Phase: **2D (Market-data operational administration)**

- ✅ Can work on: the approved Phase 2D administrative API, PostgreSQL
  operational catalog, single-host worker, RAW synchronization administration,
  minimal admin UI, recovery and tests
- ❌ Cannot work on: strategies, indicators, backtesting, distributed
  market-data storage, multi-host coordination, machine learning or live
  trading

Phase 2D must preserve the RAW, DERIVED and snapshot contracts delivered by
Phases 2A–2C. Its normative scope and limitations are documented in
[docs/ROADMAP.md](./docs/ROADMAP.md) and
[docs/adr/0001-phase-2d-operational-market-data-control-plane.md](./docs/adr/0001-phase-2d-operational-market-data-control-plane.md).

Check [docs/ROADMAP.md](./docs/ROADMAP.md) for phase definitions.

## Project Structure

Quick reference:
