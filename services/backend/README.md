# ADT Backend

FastAPI service for ADT public status and paper-simulation administration.
Phase 1 uses Supabase Auth only as the identity provider; administrator
authorization and application data come from PostgreSQL.

## Requirements

- Python 3.11 or newer;
- PostgreSQL server tools (`pg_config`, `initdb` and `pg_ctl`) for integration
  tests;
- a Supabase project using an asymmetric JWT signing key for a deployed
  environment.

No global Python installation is required. From the repository root:

```bash
python3 -m venv services/backend/.venv
services/backend/.venv/bin/python -m pip install -e 'services/backend[dev]'
```

## Configuration

The backend requires these values:

```dotenv
SUPABASE_URL=https://PROJECT_REF.supabase.co
SUPABASE_PUBLISHABLE_KEY=sb_publishable_REPLACE_ME
SUPABASE_DATABASE_URL=postgresql://USER:PASSWORD@HOST:PORT/DATABASE?sslmode=require
```

The database URL is a backend secret. Never put it in a frontend variable,
request, log or committed file. `SUPABASE_SECRET_KEY` is deliberately not used
for JWT verification.

Runtime options have safe typed defaults and can be overridden:

```dotenv
ADT_ENVIRONMENT=development
ADT_LOG_LEVEL=INFO
ADT_CORS_ORIGINS=http://localhost:5173,http://localhost:3000
ADT_API_HOST=0.0.0.0
ADT_API_PORT=8000
```

Configuration failures list only missing or invalid variable names; supplied
values are not echoed. In production, Supabase and CORS origins must be
non-local HTTPS URLs and the PostgreSQL URL must require TLS.

## Running locally

The application reads process environment variables. Export a local ignored
`.env` explicitly, then start the server:

```bash
set -a
source .env
set +a
cd services/backend
.venv/bin/python -m app.main
```

The pool opens during the FastAPI lifespan and closes cleanly during shutdown.
Health endpoints have distinct meanings:

- `GET /health`: process liveness only;
- `GET /health/database`: explicit PostgreSQL round trip;
- `GET /health/readiness`: traffic readiness, including PostgreSQL;
- `GET /api/v1/system/status`: public application version/environment metadata.

None returns a host, user, password or database URL. JWKS is fetched on demand
for administrative authentication; a temporary JWKS failure returns a safe
503 on that request without changing public liveness.

## API

Public endpoints:

- `GET /health`
- `GET /health/database`
- `GET /health/readiness`
- `GET /api/v1/system/status`
- `GET /api/v1/public/simulation`

Administrator endpoints require `Authorization: Bearer <access-token>`:

- `GET /api/v1/admin/me`
- `GET|POST /api/v1/admin/simulations`
- `GET /api/v1/admin/simulations/{simulation_id}`
- `POST /api/v1/admin/simulations/{simulation_id}/complete`
- `POST /api/v1/admin/simulations/{simulation_id}/cancel`
- `GET|POST /api/v1/admin/simulations/{simulation_id}/movements`
- `GET /api/v1/admin/settings`
- `PATCH /api/v1/admin/settings/{key}`

JWT metadata does not grant administrator access. The verified user UUID must
also exist in `public.app_admins`.

Financial values are JSON strings backed by Python `Decimal` and PostgreSQL
`numeric`. PostgreSQL constraints and triggers remain authoritative for active
simulation uniqueness, initial capital, balance and immutable history.
Numeric JSON values and scientific notation are rejected at the request
boundary.

Errors use a stable envelope:

```json
{
  "error": {
    "code": "stable_code",
    "message": "Safe client-facing message."
  }
}
```

Every response carries `X-Request-ID`; a valid inbound UUID is propagated and
an invalid/missing value is replaced. Logs contain a narrow JSON field set and
never record request bodies, authorization headers or exception messages.
Administrative responses are `no-store`. CORS origins, methods and headers are
explicit, request bodies are capped at 1 MiB, and production disables API docs
while enabling HSTS.

The browser-facing Supabase Data API has no privilege on Phase 1 base tables.
Only `active_simulation_summary` remains readable by `anon` and
`authenticated`; all administrative reads/writes go through FastAPI using the
secret direct PostgreSQL connection. Never use a `service_role` key as a
substitute for this backend boundary.

## OpenAPI contract

Pydantic/OpenAPI is the contract source for the frontend:

```bash
cd apps/web
npm run generate:api
```

The exporter injects only fictitious settings and never opens PostgreSQL or
contacts Supabase. Production disables `/docs`, `/redoc` and `/openapi.json`;
they remain available in local development with a docs-compatible CSP.

## Tests

Run the complete backend suite:

```bash
cd services/backend
.venv/bin/pytest
```

Integration tests initialize a disposable local PostgreSQL cluster and evaluate
all versioned migration SQL against isolated databases. They do not read
`SUPABASE_DATABASE_URL` and never contact the remote Supabase project.

Run quality checks:

```bash
.venv/bin/ruff check app scripts tests
.venv/bin/ruff format --check app scripts tests
.venv/bin/mypy app scripts
```

The administrator bootstrap has separate operational instructions in
[`docs/SUPABASE_SETUP.md`](../../docs/SUPABASE_SETUP.md). Running tests does not
execute `scripts/bootstrap_admin.py`.

The container runs as the unprivileged `adt` user and its Docker healthcheck
uses readiness. Distributed rate limiting and frontend CSP headers remain
deployment-edge responsibilities; see the Phase 1 homologation checklist.
