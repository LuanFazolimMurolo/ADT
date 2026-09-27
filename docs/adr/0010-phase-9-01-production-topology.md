# ADR 0010 — Phase 9-01 Production Topology

## Status

Proposed for Phase 9-01 acceptance.

## Decision

ADT will initially use a single production host with separately supervised application processes and one persistent POSIX application-data volume.

Production process boundaries:

- API: `python -m app.main`
- Market-data supervisor: `python -m app.runtime.market_data_supervisor`
- PAPER-run supervisor: `python -m app.runtime.paper_run_supervisor`

All filesystem-backed ADT processes that require persisted market data, snapshots, backtests or PAPER artifacts must observe the same persistent POSIX storage root:

`ADT_DATA_DIR=/srv/adt/data`

The authoritative relational database remains managed Supabase/PostgreSQL.

The API must not own persistent market-data collection or PAPER execution loops. Those workloads remain in their standalone supervisor processes.

The frontend is a production build of the React/Vite application. The Vite development server is not a production runtime.

Only the HTTP/TLS edge should be publicly exposed. The backend application port remains behind the reverse proxy, and the supervisors expose no public application ports.

The initial production deployment uses one host, external Supabase/PostgreSQL, persistent local POSIX storage, a TLS reverse proxy, and independently supervised API and worker processes.

Kubernetes is explicitly deferred unless measured scale or availability requirements justify it.

## Safety boundaries

Phase 9 deployment does not grant:

- LIVE trading authority;
- automatic real-capital execution;
- exchange-account authority;
- recommendation-driven execution authority;
- implicit capital authorization;
- implicit PAPER session activation.

Phase 8 recommendation approval remains decision evidence only.

## Deferred decisions

Phase 9-01 does not yet select:

- VPS/provider;
- reverse-proxy implementation;
- backup provider;
- monitoring vendor;
- CI/CD deployment transport;
- production domain;
- retention policy;
- Kubernetes.
