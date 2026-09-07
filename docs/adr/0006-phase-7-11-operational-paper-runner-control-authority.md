# ADR 0006 — Phase 7-11 Operational Paper Runner Control Authority

## Status

Accepted — Gate 1

## Date

2026-09-07

## Scope

Phase 7-11 — Operational Paper Runner Control Foundation.

This ADR defines the durable runtime-control authority between an exact
AUTHORIZED operational paper-session activation and the existing deterministic
paper-trading runner.

It does not redesign the paper engine, strategy execution, paper accounting or
RAW market-data storage.

## Context

The existing authority chain is:

OperationalMandate
-> OperationalPaperSessionProfile
-> OperationalPaperCapitalAuthorization
-> OperationalPaperSessionMaterialization
-> immutable local PaperSessionConfig
-> OperationalPaperSessionActivation

Phase 7-10 deliberately established that:

- AUTHORIZED activation is not RUNNING execution;
- activation does not own runtime state;
- activation does not own heartbeat or lease;
- activation does not own worker identity;
- activation does not own desired state; and
- activation does not own runtime epochs.

The paper subsystem already has deterministic run_once execution,
PaperTradingContinuousRunner, local filesystem exclusion and canonical
latest-cycle state.

Phase 7-11 adds the missing durable control boundary above that engine.

## Decision

Introduce a historical aggregate:

OperationalPaperSessionRunEpoch

One run epoch represents one administrator-requested execution interval for one
exact activated paper session.

A later genuine start after a terminal epoch creates a new epoch.

A terminal epoch is never reopened.

The authority chain becomes:

OperationalPaperSessionActivation
-> OperationalPaperSessionRunEpoch
-> supervisor worker claim
-> local paper-runner filesystem lease
-> deterministic run_once

## Identity

Each epoch binds immutable evidence including:

- epoch_id;
- activation_id and activation checksum;
- materialization_id and materialization checksum;
- capital authorization identity/checksum;
- profile identity/revision/checksum;
- mandate identity/revision/checksum;
- simulation_id;
- session_id;
- config_checksum;
- schema/contract versions;
- epoch_checksum;
- start actor;
- start timestamp; and
- start idempotency fingerprint.

epoch_id is a backend-generated UUID.

It is not session_id, activation_id, a checksum or an idempotency key.

## Historical epoch model

A single forever-mutable runtime row is rejected.

Repeated RUNNING -> STOPPED -> RUNNING transitions on the same identity would
create ABA ambiguity and weaken recovery and audit semantics.

Each genuine execution interval therefore has a distinct epoch identity.

One still-current AUTHORIZED activation may authorize multiple epochs over time,
but they must not overlap for the same deterministic session_id.

At most one non-terminal epoch may exist for one session_id.

## Administrator commands

The initial durable command taxonomy is:

START
PAUSE
RESUME
STOP

START creates an epoch.

PAUSE, RESUME and STOP target one exact existing epoch.

Commands represent administrator intent, not proof that a worker has already
performed the requested action.

Commands require actor identity, time, idempotency and optimistic concurrency.

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

means pause was requested but execution has not yet reached a safe pause
boundary.

Likewise:

observed_state = PAUSED
desired_state  = RUNNING

means resume was requested but execution has not yet resumed.

## Cooperative control

Pause and stop occur at deterministic paper-cycle boundaries.

7-11 does not force-kill strategy execution in the middle of run_once.

A request arriving during one execution cycle becomes effective before another
cycle is scheduled.

## Worker claim and fencing

A supervisor claims an executable epoch using durable PostgreSQL ownership.

A claim contains at minimum:

- worker_id;
- fencing_token;
- claimed_at;
- heartbeat_at; and
- lease_expires_at.

worker_id is a random operational UUID, not PID or hostname.

Every successful initial claim or reclaim advances the monotonic fencing_token.

Every worker-side mutation must prove the exact:

- epoch_id;
- worker_id;
- fencing_token;
- active lease; and
- required record_version.

record_version and fencing_token are different controls.

record_version protects optimistic concurrent mutation.

fencing_token prevents a stale worker from regaining authority after lease ABA.

## Lease and heartbeat

A PostgreSQL claim is time bounded.

The worker must renew heartbeat before lease expiry.

If lease renewal is lost, the worker fails closed and begins no new paper cycle.

After expiry another supervisor may recover the same non-terminal epoch with a
strictly higher fencing_token.

Lease expiry does not prove that the previous OS process is dead.

Therefore PostgreSQL lease ownership does not replace the existing filesystem
flock.

## Filesystem authority

The existing local paper-runner lock remains the exclusion authority for the
local ADT_DATA_DIR volume.

PostgreSQL controls operational ownership.

POSIX flock controls local process/volume exclusion.

They solve different problems and neither replaces the other.

Lock metadata such as PID remains diagnostic only.

## Supervisor boundary

FastAPI persists and reads control-plane state only.

FastAPI must not:

- launch the permanent runner loop;
- execute run_once;
- wait for physical convergence;
- own a process-lifetime local lock; or
- perform Binance market-data fetching.

A separate persistent supervisor consumes desired state and controls the
existing deterministic runner.

The existing paper execution engine is reused rather than rewritten.

PaperRunnerStateStore remains local latest-cycle evidence.

It is not start authority, desired-state authority, epoch authority, worker
claim authority or fencing authority.

## Fresh eligibility

A genuine START and every later transition back toward physical execution must
freshly validate current authority.

Required checks include:

1. activation remains AUTHORIZED;
2. exact materialization remains MATERIALIZED;
3. exact capital authorization remains AUTHORIZED;
4. bound profile remains APPROVED;
5. bound mandate remains APPROVED;
6. simulation remains ACTIVE;
7. canonical local PaperSessionConfig still exists;
8. recomputed session_id matches;
9. recomputed config_checksum matches;
10. frozen strategy plugin remains resolvable;
11. required local RAW data is present and fit for deterministic execution; and
12. no conflicting non-terminal epoch exists for the session.

Previous successful validation is never a cached execution capability.

## TOCTOU boundary

PostgreSQL and the filesystem are not one transaction.

Filesystem reads and plugin resolution must not occur while long PostgreSQL row
locks are held.

The implementation must use bounded revalidation and fail closed.

Immediately before beginning a physical paper cycle, the supervisor must still
hold the current claim/fence and must freshly verify effective execution
eligibility.

## Upstream authority loss

Revocation or invalidation of upstream authority does not synchronously kill an
OS process.

Instead, no new paper cycle may begin after authority loss is observed.

If authority is lost while RUNNING, the epoch must stop beginning new cycles and
settle fail-closed using a sanitized failure code.

If authority is lost while PAUSED, RESUME is forbidden.

STOP remains legal even when upstream authority has already been revoked,
because reducing execution must never require continued trading authority.

## Crash and recovery

A worker crash does not create a new epoch.

After lease expiry another supervisor may recover the same non-terminal epoch
with a higher fencing token.

Recovery evaluates current desired state.

If desired_state is RUNNING, current eligibility must be freshly validated.

If desired_state is PAUSED, recovery must not execute.

If desired_state is STOPPED, recovery settles the epoch without beginning a new
cycle.

If safe reconciliation is impossible, the epoch becomes FAILED.

A later administrator START after a terminal epoch creates a new epoch_id.

## Persistence direction

EXPECTED MIGRATION = YES.

A later Gate 2B migration is expected to persist:

- historical run epochs;
- exact immutable upstream bindings;
- epoch checksum;
- desired and observed state;
- record_version;
- start idempotency/fingerprint;
- append-only administrator commands;
- worker ownership;
- monotonic fencing token;
- heartbeat and lease times;
- sanitized failure state;
- terminal timestamps; and
- uniqueness rules preventing overlapping non-terminal epochs.

The migration must enable RLS.

Browser/Data API roles receive no direct runtime mutation authority.

Remote migration application remains separately controlled and is not implied by
committing the migration.

## Information disclosure

Runtime-control HTTP responses must not expose:

- ADT_DATA_DIR;
- filesystem paths;
- lock-file paths;
- hostname;
- PID;
- database URLs;
- credentials;
- raw exception text;
- SQL errors; or
- arbitrary local diagnostics.

## Explicitly out of scope

Phase 7-11 does not add:

- real-capital trading;
- exchange-account credentials;
- Binance order submission;
- live orders;
- ADT Official Portfolio;
- trading-horizon labels;
- machine learning;
- Telegram;
- SaaS;
- distributed multi-host filesystem coordination;
- arbitrary dynamic strategy imports;
- collector runtime control;
- force-killing strategy execution mid-cycle; or
- rewriting the deterministic paper engine.

## Delivery gates

R0      next-delivery selection                         CLOSED / PASS
Gate 1  runner-control architecture and epoch model     THIS ADR
Gate 2A pure run-epoch and command domain
Gate 2B PostgreSQL persistence and migration contract
Gate 2C repository, lease, fencing and recovery
Gate 2D fresh eligibility and administrator service
Gate 2E supervisor and existing-runner integration
Gate 3  protected administrator API and contract
Gate 4  optional administrator UI after explicit review
Gate 5  integration, validation and technical closure

## Consequences

The selected design intentionally introduces more durable state than a simple
runner switch.

That complexity provides:

- historical execution identity;
- clean desired/observed separation;
- explicit stale-worker fencing;
- safe crash recovery;
- fail-closed authority revalidation;
- auditable administrator commands; and
- preservation of the existing deterministic paper engine.
