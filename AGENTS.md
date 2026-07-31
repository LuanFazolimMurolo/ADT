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

Current Phase: **3A closure and validation (deterministic backtesting)**

- ✅ Can work on: immutable-snapshot backtesting, candle-by-candle execution,
  Spot long-only portfolio accounting, local risk, chained ledger, deterministic
  metrics, atomic result artifacts, verification, CLI, tests and documentation
- ❌ Cannot work on: production strategy indicators, optimization, walk-forward,
  multiple assets, real-time paper trading, live orders, leverage, shorts,
  derivatives or machine learning

Phase 3A must consume the Phase 2C snapshot contract and must not alter RAW or
DERIVED market datasets. Phase 2D operational administration remains a separate
planned control-plane track and is not implemented by backtest code.

See [docs/BACKTESTING.md](./docs/BACKTESTING.md) and
[docs/ROADMAP.md](./docs/ROADMAP.md).

## Project Structure

Quick reference:
