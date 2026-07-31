# ADT Backend

FastAPI service for ADT public status, paper-simulation administration and the
local Phase 2A/2B historical market-data foundation and resumable ingestion.
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
ADT_DATA_DIR=./data
ADT_MARKET_HTTP_TIMEOUT=10
ADT_MARKET_HTTP_MAX_CONNECTIONS=4
ADT_MARKET_HTTP_RETRIES=3
ADT_MARKET_HTTP_MAX_RETRY_AFTER=30
ADT_MARKET_USER_AGENT=ADT-MarketData/0.1
ADT_MARKET_ALLOW_OPEN_CANDLES=false
ADT_MARKET_MAX_FETCH_CANDLES=10000
ADT_MARKET_BACKFILL_CHUNK_CANDLES=1000
ADT_MARKET_BACKFILL_MAX_TOTAL_CANDLES=1000000
ADT_MARKET_INCREMENTAL_OVERLAP_CANDLES=2
ADT_MARKET_JOB_LOCK_TIMEOUT=10
ADT_MARKET_JOB_STALE_AFTER=3600
ADT_MARKET_JOB_MAX_CHUNKS=10000
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

## Market-data CLI

Phases 2A and 2B store candles locally and do not add an HTTP route or worker. The
commands are:

```bash
.venv/bin/python -m app.cli market-data instruments \
  --exchange binance --market spot

.venv/bin/python -m app.cli market-data fetch \
  --exchange binance --market spot --symbol BTC/USDT --timeframe 1h \
  --start 2026-01-01T00:00:00Z --end 2026-01-02T00:00:00Z --dry-run

.venv/bin/python -m app.cli market-data inspect \
  --exchange binance --market spot --symbol BTC/USDT --timeframe 1h

.venv/bin/python -m app.cli market-data verify \
  --exchange binance --market spot --symbol BTC/USDT --timeframe 1h \
  --start 2026-01-01T00:00:00Z --end 2026-01-02T00:00:00Z

.venv/bin/python -m app.cli market-data backfill plan \
  --symbol BTC/USDT --timeframe 1h \
  --start 2025-01-01T00:00:00Z --end 2025-02-01T00:00:00Z

.venv/bin/python -m app.cli market-data backfill run \
  --symbol BTC/USDT --timeframe 1h \
  --start 2025-01-01T00:00:00Z --end 2025-02-01T00:00:00Z --yes

.venv/bin/python -m app.cli market-data backfill status --job-id JOB_UUID
.venv/bin/python -m app.cli market-data backfill pause --job-id JOB_UUID
.venv/bin/python -m app.cli market-data backfill cancel --job-id JOB_UUID
.venv/bin/python -m app.cli market-data backfill resume \
  --job-id JOB_UUID --symbol BTC/USDT
.venv/bin/python -m app.cli market-data update --symbol BTC/USDT --timeframe 1h
.venv/bin/python -m app.cli market-data gaps --symbol BTC/USDT --timeframe 1h \
  --start 2025-01-01T00:00:00Z --end 2025-02-01T00:00:00Z
.venv/bin/python -m app.cli market-data repair --symbol BTC/USDT --timeframe 1h \
  --start 2025-01-01T00:00:00Z --end 2025-02-01T00:00:00Z --yes
```

`backfill plan/status`, `gaps`, `inspect` and `verify` are local-only. Commands
that execute fetching use the public Binance market-data endpoint.
Multi-chunk writes require `--yes`; Phase 2B `--dry-run` plans without network
or local writes. Output is a bounded JSON summary and never includes configured
secrets or raw source errors.

`ADT_MARKET_MAX_FETCH_CANDLES` is checked before instrument lookup or any HTTP
request. `ADT_MARKET_ALLOW_OPEN_CANDLES=true` permits open candles only in
adapter and diagnostic/dry-run results; persistent Parquet datasets always
contain closed candles exclusively.

Phase 2B keeps immutable job plans and atomic checkpoints in
`ADT_DATA_DIR/market/jobs.json`. A Linux advisory lock prevents concurrent
writes to the same exchange/market/symbol/timeframe dataset. Each confirmed
chunk is a complete Phase 2A journal transaction; failed or paused jobs resume
from the first unconfirmed range. Incremental updates use a configured overlap,
and gap repair is always explicit and source-backed.

All persistent entry points share the same explicit dataset lease. The main
catalog uses a separate global `.catalog.lock`, held from its fresh completion
read through the journal's durable commit. Chunk receipts are committed inside
that catalog transaction, so resume can restore original metrics without
refetching after a post-commit checkpoint interruption. CLI job startup safely
marks only unlocked abandoned jobs as `FAILED`; kernel `flock`, never PID or
age metadata, decides whether a job is active.

The optional minimal network smoke test is disabled by default and must be
selected explicitly:

```bash
ADT_ALLOW_NETWORK_TESTS=true .venv/bin/pytest \
  tests/manual/test_market_network_smoke.py -q
```

Do not include that file in routine automated gates. Full dataset format and
recovery details are in
[`docs/MARKET_DATA.md`](../../docs/MARKET_DATA.md).

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
Market adapter tests inject `httpx.MockTransport`; the optional network smoke
test remains skipped unless `ADT_ALLOW_NETWORK_TESTS=true`.

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
