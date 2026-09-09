# ADR 0007 — Phase 7-12 Operational Market-Data Collector Control Authority

## Status

Accepted — Gate 1

## Date

2026-09-08

## Scope

Phase 7-12 — Operational Market-Data Collector Control Foundation.

This ADR defines durable administrative runtime-control authority for the
existing continuous Binance Spot RAW market-data collector.

It does not redesign candle acquisition, incremental planning, Parquet
persistence, dataset locking, market-operation workers or the existing
continuous collection algorithm.

## Context

Phase 7 already provides two different market-data execution mechanisms:

1. bounded PostgreSQL-backed market-data operations processed by the existing
   market-operation worker; and
2. the older continuous RAW collector implemented by
   `ContinuousCollectionService` and `ContinuousCollectionRunner`.

The bounded market-operation path already has durable operation identity,
pause/resume/cancel semantics, worker leases, recovery and observability.

The continuous collector is different.

Its current execution contract is local and process-oriented:

- targets are supplied when the CLI process starts;
- `ContinuousCollectionRunner` owns a continuous loop;
- one global collector lock prevents concurrent local collection loops;
- `ContinuousCollectionStateStore` persists only latest complete-cycle
  evidence;
- the collector does not have PostgreSQL start authority;
- the collector does not have durable desired state;
- the collector does not have administrator command history;
- the collector does not have worker fencing; and
- pause/resume/stop are not durable control-plane operations.

Phase 7-11 deliberately excluded `collector runtime control`.

Phase 7-12 closes that specific boundary without replacing the existing
collector engine.

## Decision

Introduce a historical aggregate:

`OperationalMarketDataCollectorEpoch`

One epoch represents one administrator-requested execution interval for one
exact immutable continuous-collector specification.

A later genuine START after a terminal epoch creates a new epoch.

A terminal epoch is never reopened.

The authority chain becomes:

administrator command
-> OperationalMarketDataCollectorEpoch
-> supervisor worker claim
-> existing global local collector lock
-> existing continuous collection service
-> canonical RAW datasets and latest local collection state

## Collector scope

The initial operational collector scope is closed and singular:

`BINANCE_SPOT_RAW`

Phase 7-12 does not introduce arbitrary exchanges, arbitrary market types or
multiple independently concurrent collector scopes.

The current collector owns one global local lock for the whole continuous RAW
collection facility. PostgreSQL authority must reflect that physical boundary.

At most one non-terminal operational collector epoch may therefore exist for
the `BINANCE_SPOT_RAW` scope.

Future support for independently isolated collector scopes requires a new
reviewed contract and must not be inferred from this ADR.

## Epoch identity

Each epoch has a backend-generated UUID:

`epoch_id`

It is not a PID, hostname, checksum, idempotency key, dataset id or cycle id.

Each epoch also binds immutable evidence including:

- epoch_id;
- collector scope;
- canonical collector specification;
- collector specification checksum;
- start actor;
- start timestamp;
- start idempotency key/fingerprint;
- desired state;
- observed state;
- record version;
- optional current worker claim;
- failure state; and
- terminal timestamps.

## Immutable collector specification

A genuine START freezes one exact collector specification for the lifetime of
the epoch.

The specification contains:

- ordered unique targets;
- exact trading pair for every target;
- exact canonical timeframe for every target;
- bootstrap candle count for every target;
- effective collection interval;
- effective overlap-candle policy; and
- explicit schema/contract version.

Exchange and market are fixed to Binance Spot for this track.

The target list must be canonical, unique and deterministically ordered.

The specification has a canonical checksum.

Environment/settings defaults may be used while constructing a new START
request, but the resulting effective values must be persisted. A running epoch
must not silently change behavior because process environment defaults later
change.

## Configuration changes

Phase 7-12 does not mutate a running collector specification in place.

Changing any execution-defining field, including:

- target membership;
- target timeframe;
- bootstrap size;
- collection interval; or
- overlap policy

requires stopping the current epoch and issuing a genuine new START.

The new START creates a new epoch with a new immutable specification.

This preserves historical identity and prevents configuration ABA.

## Administrator commands

The initial command taxonomy is:

START
PAUSE
RESUME
STOP

START creates an epoch.

PAUSE, RESUME and STOP target one exact existing epoch.

Commands represent administrator intent, not evidence that physical execution
already converged.

Commands require:

- administrator actor identity;
- timestamp;
- idempotency;
- optimistic concurrency; and
- immutable append-only command history.

STOP must remain legal even if the collector is already experiencing local or
worker-side failure because reducing execution must not require continued
execution authority.

## Desired state

The initial desired states are:

RUNNING
PAUSED
STOPPED

A new epoch starts with:

desired_state = RUNNING

PAUSE changes desired_state to PAUSED.

RESUME changes desired_state to RUNNING.

STOP changes desired_state to STOPPED.

Desired state belongs to PostgreSQL control-plane authority.

## Observed state

The initial observed states are:

PENDING
STARTING
RUNNING
PAUSED
RECOVERING
STOPPING
STOPPED
FAILED

STOPPED and FAILED are terminal.

Desired and observed states are deliberately separate.

For example:

observed_state = RUNNING
desired_state  = PAUSED

means pause has been requested but the current collection cycle has not yet
reached its safe boundary.

Likewise:

observed_state = PAUSED
desired_state  = RUNNING

means resume has been requested but a worker has not yet begun the next
collection cycle.

## Initial observed-state graph

The permitted initial graph is:

PENDING
-> STARTING
-> PAUSED
-> STOPPED
-> FAILED

STARTING
-> RUNNING
-> PAUSED
-> RECOVERING
-> STOPPING
-> FAILED

RUNNING
-> PAUSED
-> RECOVERING
-> STOPPING
-> FAILED

PAUSED
-> STARTING
-> STOPPED
-> FAILED

RECOVERING
-> STARTING
-> PAUSED
-> STOPPING
-> STOPPED
-> FAILED

STOPPING
-> RECOVERING
-> STOPPED
-> FAILED

STOPPED and FAILED have no outgoing transitions.

The domain implementation may encode the graph more strictly, but it must not
introduce a transition that reopens a terminal epoch.

## Cooperative cycle boundary

PAUSE and STOP are cooperative collector-cycle operations.

Phase 7-12 does not abort:

- a target halfway through its incremental update;
- an HTTP request solely because desired state changed;
- a dataset write halfway through publication; or
- a complete collection cycle halfway through its ordered target set.

A command arriving during a physical collection cycle becomes effective before
another cycle is scheduled.

No new collection cycle may begin after the worker observes desired PAUSED or
STOPPED.

This preserves existing deterministic and atomic local dataset boundaries.

## Existing target-failure semantics

The existing collector deliberately isolates target failures inside one cycle.

Phase 7-12 preserves that behavior.

A target-level failure may produce:

- UPDATED;
- NOOP; or
- FAILED

inside the existing `ContinuousCollectionState`.

An aggregate collection cycle may therefore be:

- COMPLETED;
- PARTIALLY_FAILED; or
- FAILED.

A failed target or even one failed collection cycle does not automatically make
the operational epoch FAILED.

The collector may retry targets on later cycles according to the existing
continuous collection behavior.

The epoch becomes FAILED only for failures that invalidate safe operational
control or prevent the controlled collector from continuing safely.

## Worker claim and fencing

A persistent supervisor claims an executable epoch using PostgreSQL ownership.

A worker claim contains at minimum:

- worker_id;
- fencing_token;
- claimed_at;
- heartbeat_at; and
- lease_expires_at.

worker_id is a random operational UUID.

It must not be derived from PID or hostname.

Every successful initial claim or reclaim advances a monotonic positive
fencing_token.

Every worker-side PostgreSQL mutation must prove the exact:

- epoch_id;
- worker_id;
- fencing_token;
- active lease where required; and
- expected record_version.

record_version and fencing_token remain separate concurrency controls.

record_version protects optimistic mutation.

fencing_token prevents stale-worker ABA after lease recovery.

## Lease and heartbeat

A PostgreSQL worker claim is time bounded.

The worker must renew heartbeat while it owns physical execution authority.

If lease renewal is lost:

- the worker must begin no new collection cycle;
- stale PostgreSQL mutation must fail closed; and
- another supervisor may recover the same non-terminal epoch only after the
  existing claim is recoverable under the lease contract.

Lease loss does not force-kill an in-progress collection cycle.

The in-progress cycle may reach its safe local completion boundary, but the
stale worker may not begin another cycle and may not regain PostgreSQL
authority without a valid reclaim.

## Crash and recovery

A worker crash does not create a new epoch.

Recovery uses the same epoch with a strictly higher fencing token.

Recovery evaluates current desired state.

If desired_state is RUNNING:

- recover the same epoch;
- validate the immutable collector specification;
- validate canonical local collection state;
- acquire the existing local collector lock before physical collection; and
- begin a later cycle only while the recovered claim remains current.

If desired_state is PAUSED:

- recover/control the epoch if necessary;
- do not execute a collection cycle; and
- converge observed state to PAUSED.

If desired_state is STOPPED:

- settle the epoch without beginning a new collection cycle.

If safe local reconciliation is impossible, the epoch becomes FAILED.

A later administrator START after terminal STOPPED or FAILED creates a new
epoch_id.

## Local collection state

`ContinuousCollectionStateStore` remains canonical latest complete-cycle
evidence for the existing local collector engine.

It is not:

- START authority;
- PAUSE authority;
- RESUME authority;
- STOP authority;
- desired-state authority;
- epoch authority;
- worker-claim authority; or
- fencing authority.

The controlled worker may read valid latest-cycle state to determine the next
monotonic collection cycle index.

A previously completed local cycle from an earlier epoch or explicit local CLI
operation does not itself create, resume or authorize an operational epoch.

Corrupt or non-canonical local collection state must fail closed for controlled
execution.

## PostgreSQL authority versus local lock

PostgreSQL and the existing local collector lock solve different problems.

PostgreSQL owns:

- administrative intent;
- epoch identity;
- immutable collector specification;
- desired/observed state;
- administrator commands;
- worker ownership;
- leases;
- fencing; and
- recovery authority.

The existing global local collector lock owns:

- process/volume exclusion for the local continuous collector.

The PostgreSQL lease does not replace the local lock.

The local lock does not replace PostgreSQL authority.

Immediately before beginning a physical collection cycle, the worker must:

1. still own the current PostgreSQL claim/fence;
2. observe desired_state = RUNNING;
3. validate the frozen collector specification;
4. validate canonical local collection state;
5. acquire the existing global local collector lock; and
6. re-check current worker authority before beginning physical work.

The implementation must not hold a long PostgreSQL row lock while performing
filesystem access or network I/O.

## Local lock lifetime

The controlled worker must hold the existing global collector lock for the full
physical collection cycle.

It is not required to hold the local lock while the epoch is PAUSED or while
the supervisor is sleeping between cycles.

A concurrent legacy/manual collector that already owns the same local lock
must prevent the controlled worker from beginning a cycle.

The controlled worker must fail closed or remain safely non-executing according
to the reviewed worker contract; it must never bypass the existing lock.

## CLI compatibility

Existing explicit local CLI collection workflows remain supported.

Phase 7-12 does not silently convert every local CLI invocation into a
PostgreSQL administrator command.

The local CLI therefore remains an explicit out-of-band operator path.

It must continue using the same local exclusion mechanism.

An out-of-band CLI invocation does not gain:

- operational epoch identity;
- PostgreSQL desired-state authority;
- worker fencing; or
- administrator command history.

Production deployment policy may later prohibit out-of-band CLI collectors
while an operational host is active, but that host/deployment policy is not
created by this ADR.

## Supervisor boundary

FastAPI persists and reads collector control-plane state only.

FastAPI must not:

- launch the permanent collector loop;
- execute a collection cycle;
- hold the process-lifetime collector lock;
- wait for physical convergence;
- perform collector scheduling sleeps; or
- perform Binance collection network I/O as part of a control command.

A separate persistent supervisor consumes PostgreSQL desired state and controls
the existing collection engine.

The existing collector algorithm is reused rather than rewritten.

## START validation

A genuine START must validate, at minimum:

- collector scope is the supported closed scope;
- target list is non-empty and bounded;
- targets are unique;
- targets are canonically ordered;
- trading pairs are syntactically valid;
- every timeframe belongs to the canonical timeframe registry;
- bootstrap values are valid;
- effective interval is valid;
- effective overlap policy is valid;
- collector specification checksum is valid; and
- no conflicting non-terminal operational collector epoch exists.

START control-plane persistence must not execute a physical collection cycle.

Network/instrument failures continue to be resolved by the existing physical
collector path and remain target-level collection outcomes unless they become a
fatal framework failure.

## RESUME validation

RESUME changes desired state only when the target epoch is resumable.

Before a resumed physical cycle begins, the worker must again validate:

- exact persisted collector specification;
- canonical local state;
- current claim/fence;
- current desired state; and
- local collector exclusion.

Previous successful execution is never a cached capability to begin another
cycle.

## Failure taxonomy

The initial closed operational failure-code taxonomy is:

COLLECTOR_SPEC_INVALID
LOCAL_COLLECTOR_BUSY
LOCAL_STATE_INVALID
LEASE_LOST
DATABASE_UNAVAILABLE
INTERNAL_ERROR

Raw exception strings, filesystem paths, database URLs and network internals
must not become persisted public failure codes.

Target-level collection failures remain in existing sanitized target/cycle
evidence and are not mapped automatically into epoch failure.

The implementation may narrow names during Gate 2A, but it must preserve a
closed sanitized taxonomy.

## Persistence direction

EXPECTED MIGRATION = YES.

Gate 2B is expected to persist:

- historical collector epochs;
- collector scope;
- immutable canonical collector specification;
- specification checksum;
- desired state;
- observed state;
- record_version;
- start idempotency/fingerprint;
- append-only administrator commands;
- worker ownership;
- monotonic fencing token;
- heartbeat and lease times;
- sanitized failure state;
- terminal timestamps; and
- uniqueness preventing overlapping non-terminal epochs for the supported
  collector scope.

The migration must enable RLS.

Browser/Data API roles receive no direct collector-control mutation authority.

Remote migration application remains separately controlled and is not implied
by committing the migration.

## Information disclosure

Collector-control HTTP responses must not expose:

- ADT_DATA_DIR;
- filesystem paths;
- collector lock paths;
- hostname;
- PID;
- database URLs;
- credentials;
- raw exception text;
- SQL errors;
- arbitrary network errors; or
- internal idempotency fingerprints.

Public administrator responses may expose sanitized operational state,
immutable target specification, record_version, fencing token and bounded lease
timing if Gate 3 confirms those fields are necessary for operational
diagnostics.

worker_id remains internal unless a later explicit review proves disclosure is
necessary.

## API direction

Gate 3 is expected to expose a protected administrator-only collector-control
surface.

The initial capability set is expected to cover:

- START;
- GET one epoch;
- bounded command-history reads;
- PAUSE;
- RESUME; and
- STOP.

Exact route naming and response schemas remain Gate 3 implementation details,
but FastAPI must remain control-plane only.

## Relationship to bounded market-data operations

Operational collector epochs do not replace
`MarketOperation`.

Bounded market-data operations remain the durable queue for explicit bounded
BACKFILL and INCREMENTAL administrative work.

The operational collector is a persistent cadence authority for repeatedly
maintaining a frozen set of RAW datasets.

The two mechanisms must not share aggregate identity merely because both can
write market data.

Existing dataset-level locks continue preventing unsafe local write overlap.

## Relationship to worker observability

Existing market-operation worker-runtime observability is not automatically the
collector epoch authority.

Phase 7-12 may reuse observability patterns where appropriate, but:

- collector epoch state;
- collector worker claim;
- collector fencing; and
- collector command history

belong to the new collector-control aggregate.

A later persistent operational host may expose combined host-level
observability for market-operation workers, collector supervisors and paper
supervisors.

## Persistent host boundary

Phase 7-12 creates collector control authority and the process-level
supervisor/worker integration needed to consume it.

It does not define the final production service-manager deployment for all ADT
persistent workers.

A later reviewed delivery may own:

- unified worker-host process composition;
- systemd/container/service-manager definitions;
- restart policy;
- host-level readiness;
- combined process health; and
- deployment-time prohibition of conflicting manual loops.

That later host work must consume the authorities created here rather than
replacing them.

## Explicitly out of scope

Phase 7-12 does not add:

- real-capital trading;
- exchange-account credentials;
- Binance order submission;
- live orders;
- ADT Official Portfolio;
- trading-horizon labels;
- arbitrary exchange support;
- arbitrary market support;
- multiple concurrent collector scopes;
- dynamic target mutation inside an active epoch;
- redesign of `ContinuousCollectionService`;
- redesign of RAW Parquet storage;
- redesign of bounded market-data operations;
- force-killing an in-progress target or collection cycle;
- distributed multi-host filesystem coordination;
- final service-manager deployment; or
- unrelated Phase 2D administrative scope.

## Delivery gates

R0      next-delivery selection                              CLOSED / PASS
Gate 1  collector-control architecture and epoch model        THIS ADR
Gate 2A pure collector epoch/specification/command domain
Gate 2B PostgreSQL persistence and migration contract
Gate 2C repository, lease, fencing and recovery
Gate 2D administrator service and control-plane validation
Gate 2E supervisor and existing-collector integration
Gate 3  protected administrator API and generated contract
Gate 4  optional administrator UI after explicit review
Gate 5  integration, validation and technical closure

## Consequences

The selected design adds durable control state above an intentionally local
collector engine.

That additional state provides:

- historical collector execution identity;
- immutable target/policy snapshots;
- explicit administrator START/PAUSE/RESUME/STOP intent;
- desired/observed separation;
- stale-worker fencing;
- crash recovery;
- bounded operational audit history; and
- a clean boundary for a later persistent worker host.

The design deliberately preserves:

- the existing collection algorithm;
- target-level failure isolation;
- canonical RAW dataset semantics;
- latest-cycle local state;
- dataset/local exclusion;
- explicit CLI workflows; and
- the rule that FastAPI is not a long-running worker host.
