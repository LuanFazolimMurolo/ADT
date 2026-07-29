# ADT Architecture

## Overview

ADT follows a **layered, modular architecture** with clear separation of concerns.

The backend is the only application component allowed to perform administrative
writes. The frontend remains a presentation layer and sends the Supabase access
token in the standard Bearer header.

```text
React client
    │ Authorization: Bearer <Supabase access token>
    ▼
FastAPI routes
    ▼
authentication and administrator dependencies
    ▼
application services
    ▼
repositories
    ▼
async psycopg connection pool
    ▼
PostgreSQL / Supabase
```

## Backend layers

The FastAPI backend is split into the following boundaries:

- `app/api/routes/`: HTTP paths, status codes and dependency composition;
- `app/api/dependencies/`: request authentication and administrator checks;
- `app/api/schemas/`: explicit request and response contracts;
- `app/auth/`: local JWT verification against the Supabase JWKS endpoint;
- `app/services/`: transaction-level application use cases;
- `app/repositories/`: parameterized SQL and row mapping;
- `app/domain/`: typed entities, enums and safe domain errors;
- `app/database/`: asynchronous pool lifecycle and transaction contexts;
- `app/core/`: typed configuration and logging.

Routes do not contain SQL or financial rules. Repositories do not know about
FastAPI, and domain/services do not return HTTP responses.

## Authentication and authorization

Supabase Auth is the identity provider. The backend validates access tokens
locally with asymmetric public keys from the project's JWKS endpoint. Signature,
issuer, `authenticated` audience and expiration are mandatory. Accepted user
identity comes only from the verified `sub` UUID.

The publishable key is configuration, not proof of administrator access. The
Supabase secret key is not used by this backend.

Administrative authorization is database-backed: after authentication, a
dependency queries `public.app_admins` for the verified UUID. JWT metadata is
never treated as an administrator grant. Missing or invalid authentication
returns 401; an authenticated UUID absent from the allow-list returns 403.

## Persistence and transactions

The backend uses asynchronous psycopg connections from a bounded pool.
Application services open explicit transactions and pass one connection through
all repositories involved in a use case. Pool startup and shutdown follow the
FastAPI lifespan.

Creating a simulation and its `INITIAL_CAPITAL` ledger entry is one transaction.
Movement metadata is stored as the immutable audit record associated with the
new movement. Settings updates also record the acting administrator.

Financial correctness remains authoritative in PostgreSQL:

- all amounts are `numeric` in PostgreSQL and `Decimal` in Python;
- the unique active simulation and unique initial capital are database indexes;
- the initial-capital and non-negative-balance rules are database triggers;
- ledgers and historical simulation fields are protected by database triggers.

Python validation improves client feedback but never replaces these constraints.
Known PostgreSQL violations are translated to stable, non-sensitive domain
errors; raw SQL messages and connection details are never returned to clients.

## Public data boundary

The public simulation endpoint reads only
`public.active_simulation_summary`. Its response schema deliberately omits the
view's internal simulation UUID and cannot expose administrator, audit or
movement-level data.

## Phase 1C frontend

The React application keeps the public site at `/` and exposes no registration
or visible login entry there. Administrative authentication starts only under
`/admin`. The frontend is split into these boundaries:

- `config/` validates the three public `VITE_*` variables without echoing
  configured values;
- `lib/supabase.ts` is the single Supabase client, with SDK-managed session
  persistence, automatic token refresh and URL session detection;
- `auth/` restores the session, confirms every administrative session through
  `GET /api/v1/admin/me`, protects routes and performs logout;
- `http/` is the only FastAPI client and attaches the current Supabase access
  token to administrative requests;
- `pages/admin/` renders backend contracts without performing financial
  calculations or direct database access.

The API client retries a failed authentication only for idempotent `GET`
requests, after asking the Supabase SDK to refresh the session. It never
automatically repeats `POST` or `PATCH`, preventing duplicate simulations,
ledger entries or setting updates. A persistent 401 ends the local session;
401/403 during administrator verification denies the private route.

Financial decimals remain strings across the JSON boundary. Withdrawal signs
are mapped to the backend request contract, adjustments preserve the explicit
sign, and balances/P&L displayed by the UI are always values calculated and
returned by the backend.

## Out of scope

Phase 1B does not add frontend login screens, strategy execution, backtesting,
market adapters, Telegram integration, machine learning or real-capital
trading.
