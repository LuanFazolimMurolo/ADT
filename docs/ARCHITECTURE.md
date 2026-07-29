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

## Out of scope

Phase 1B does not add frontend login screens, strategy execution, backtesting,
market adapters, Telegram integration, machine learning or real-capital
trading.
