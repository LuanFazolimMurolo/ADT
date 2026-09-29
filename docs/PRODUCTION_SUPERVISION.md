# ADT Production Process Supervision

## Scope

Production runs four independently supervised containers:

- API;
- market-data supervisor;
- paper-run supervisor;
- web frontend.

This contract defines host/container lifecycle behavior only.

It does not grant trading authority, activate PAPER sessions, select strategies,
allocate capital or enable LIVE execution.

## Restart policy

All production containers use:

`restart: unless-stopped`

This means an unexpected process exit or host/container-engine restart may cause
the process to be started again.

Automatic process restart does not create new application authority.

The market-data and paper supervisors must still rely on their persisted domain
state, leases, fencing tokens and normal eligibility checks.

In particular, restarting the paper supervisor does not itself authorize:

- START;
- RESUME;
- capital use;
- order creation;
- recommendation-driven activation;
- LIVE trading.

## Shutdown signal

Production explicitly uses:

`stop_signal: SIGTERM`

The backend supervisor entrypoints already install cooperative SIGTERM/SIGINT
handlers and request a controlled supervisor stop.

The runtime is expected to stop claiming new work, allow the current bounded
operation to converge through its existing shutdown behavior and close database
resources before process exit.

## Shutdown grace periods

Backend processes use:

`stop_grace_period: 60s`

The frontend Nginx process uses:

`stop_grace_period: 15s`

The backend grace period is intentionally conservative. It allows cooperative
shutdown and connection cleanup while still providing a finite upper boundary
before the container runtime may force termination.

This grace period is infrastructure tolerance, not a promise that every market
or paper operation completes within sixty seconds.

## Health boundaries

The API health model remains intentionally separated:

- `/health` — API process liveness;
- `/health/database` — PostgreSQL dependency probe;
- `/health/readiness` — API traffic readiness.

The production Compose healthcheck uses API readiness.

The web container exposes its own local HTTP health endpoint.

The market-data and paper supervisors do not receive synthetic container
healthchecks in this delivery.

A process-only healthcheck for those supervisors would be misleading because:

`container running != supervisor useful`
`supervisor useful != market data fresh`
`paper supervisor running != paper session active`
`paper session active != trading authorized`

Their operational health belongs to application-level heartbeat, lease,
freshness and observability evidence.

## Failure and recovery

Container restart must not bypass application recovery rules.

Existing runtime protections remain authoritative, including:

- durable desired/observed state;
- worker leases;
- heartbeats;
- fencing tokens;
- stale-worker rejection;
- restart reconciliation;
- persisted PAPER authorization boundaries.

A restarted worker must rediscover state through the normal repositories and
must not fabricate a new epoch, command, activation or authorization merely
because its process restarted.

## Persistent storage

API, market supervisor and paper supervisor continue to share the persistent
production `ADT_DATA_DIR`:

`/srv/adt/data`

Container replacement or restart must not replace this host-backed storage.

## PID 1 / init process

`init: true` is not introduced in this delivery.

The current production processes execute one foreground application process and
do not establish a demonstrated subprocess-reaping requirement that justifies
changing the PID 1 model.

This decision may be revisited if future runtime behavior introduces child
processes or measured zombie-process risk.

## Monitoring boundary

This delivery provides process supervision and bounded shutdown behavior.

It does not replace Phase 9 monitoring and alerting.

Future monitoring must distinguish at minimum:

- process/container availability;
- database readiness;
- supervisor heartbeat health;
- worker lease health;
- market-data freshness;
- PAPER-run freshness/state;
- application errors.

## Operational boundary

This document does not authorize:

- deployment;
- host restart;
- container restart;
- production process start/stop;
- migration application;
- PAPER activation;
- LIVE execution.

It only defines the versioned supervision contract.
