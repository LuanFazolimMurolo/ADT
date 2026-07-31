# ADR 0001: Phase 2D operational market-data control plane

- **Status**: Accepted
- **Date**: 2026-07-31
- **Scope**: Phase 2D

## Context

Phases 2A–2C provide durable local RAW ingestion, resumable jobs, deterministic
derived datasets and immutable snapshots. Their correctness boundary is local:
Parquet and JSON metadata live below `ADT_DATA_DIR`, transactions use
`PREPARED/COMMITTED` journals, chunk commits have receipts, and Linux `flock`
serializes a dataset on one host.

Phase 2D must let authenticated administrators plan, submit and observe RAW
synchronization without weakening those guarantees. HTTP requests are
short-lived, while backfills can perform many network requests and durable
filesystem transactions. API retries, process restarts and worker crashes must
not duplicate an operation or refetch a confirmed chunk.

## Decision

### Separate HTTP and worker processes

FastAPI persists administrative intent and returns. A separate permanent
market-data worker claims and executes operations. The worker also supports
`run-once` for controlled tests and manual operation.

FastAPI `BackgroundTasks` is not used because it:

- belongs to the HTTP process lifecycle;
- has no durable queue or claim;
- can disappear on deploy, crash or process recycling;
- does not provide cross-process lease, heartbeat or recovery;
- can duplicate work after an HTTP retry;
- cannot safely establish one-worker-per-volume ownership.

### PostgreSQL is the operational control plane

PostgreSQL is authoritative for:

- administrative intent and requester;
- explicit idempotency key and payload identity;
- externally visible operation state;
- worker claim, lease and heartbeat;
- pause/cancel requests;
- sanitized progress, result and failure.

Claims use `FOR UPDATE SKIP LOCKED` or an equivalent atomic operation and are
committed before network or filesystem work. No PostgreSQL transaction may
remain open while waiting for `flock`, Binance, Parquet processing or `fsync`.

Operational tables have RLS enabled and no privileges for Supabase Data API
roles. They contain no candle rows, credentials, raw source payloads or
filesystem paths supplied by clients. The backend direct PostgreSQL connection
is the only operational writer.

### Local storage remains the execution and dataset authority

PostgreSQL does not replace:

- RAW Parquet;
- `catalog.json`;
- `jobs.json`;
- immutable chunk receipts;
- ingestion journals;
- dataset `flock`;
- the existing local recovery rules.

Local state is authoritative for the effective plan, confirmed chunks,
physical/logical integrity and commit result. PostgreSQL can lag after a crash
and must then be reconciled from verified local state. A durable `COMMITTED`
journal is success even if cleanup fails, and a confirmed receipt must never
cause another source fetch.

Storing candles in PostgreSQL was rejected because Phase 2 deliberately uses
Parquet for bounded analytical reads and already has a tested durability
protocol. Mirroring candle data would introduce dual-write consistency without
solving local execution recovery.

### Single-host, single-volume topology

Phase 2D assumes:

- one operational host;
- one persistent POSIX `ADT_DATA_DIR`;
- API and worker on the same host or exact same mounted volume;
- one active market-data worker per volume;
- one active operation per worker.

PostgreSQL coordinates queue ownership; `flock` coordinates local dataset
access. This combination is not claimed to coordinate files across hosts.
Distributed storage, uncertain network-filesystem lock semantics and
multi-host workers require a different storage/locking protocol and are
explicitly deferred.

### Cooperative lifecycle

Pause and cancellation are requests, not asynchronous interruption. The worker
observes them before execution, between chunks, before a new source request and
after a local commit. It never interrupts a local journal transaction.

The normative state machine and transition matrix live in
[`docs/ARCHITECTURE.md`](../ARCHITECTURE.md#operation-state-machine).
Terminal states are immutable. Resume moves `PAUSED` back to `PENDING` for a
new claim. A late pause/cancel request may resolve to `COMPLETED` when the local
commit was already durable.

### Retention

Operations and events have an initial 30-day retention policy. Phase 2D does
not automatically delete them and adds no cleanup scheduler. A later phase
must define an audited cleanup operation before enforcing retention.

## Alternatives considered

### Execute inside the API request

Rejected because large jobs exceed ordinary HTTP lifetimes and couple client
disconnects to operational execution.

### FastAPI `BackgroundTasks`

Rejected for lack of durability, claim, lease, process isolation and restart
recovery.

### PostgreSQL-only execution state

Rejected because it cannot determine whether Parquet/catalog journal commit
became durable at a crash boundary. Local receipts and journals remain
necessary.

### Local-only administrative queue

Rejected because API and worker are separate processes and administrative
submission requires durable idempotency, authorization attribution and a
stable query surface.

### Distributed/multi-host worker

Deferred. Existing `flock`, hard-link snapshots and local path containment are
single-filesystem contracts.

## Consequences

Positive consequences:

- HTTP retries cannot create duplicate operations when idempotency is used;
- API restarts do not lose queued work;
- worker restarts can reconcile from durable local state;
- existing RAW/DERIVED/snapshot formats remain unchanged;
- no candle duplication is introduced in PostgreSQL;
- administrative state can be queried without reading local job files.

Costs and risks:

- PostgreSQL and local execution state form two deliberately different
  authorities and require explicit reconciliation;
- deployment must enforce one worker per volume;
- heartbeat expiry must not be mistaken for proof that an old process stopped;
- API and worker configuration must resolve to the same `ADT_DATA_DIR`;
- automatic 30-day cleanup remains unresolved;
- scaling to multiple hosts requires a new ADR and storage protocol.

## Completion evidence

Phase 2D is complete only when:

- submission idempotency and state transitions are enforced and tested;
- claims commit before external/local work;
- no two operations execute on the same dataset;
- crash tests cover every local commit/reconciliation boundary;
- confirmed receipts avoid refetch;
- abandoned leases converge idempotently;
- API and worker shut down cleanly;
- Data API roles cannot access operational tables;
- administrative UI uses only the authenticated FastAPI contract;
- existing Phase 2A–2C dataset compatibility tests still pass;
- controlled operational validation proves restart and recovery behavior.
