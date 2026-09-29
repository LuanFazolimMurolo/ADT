# Production Official PAPER Availability and Freshness

## Scope

This document defines the Phase 9 availability and freshness boundaries for the
ADT official PAPER trader.

The contract separates:

- supervisor process health;
- operational PAPER control-plane state;
- worker ownership freshness;
- execution convergence;
- failure evidence.

Availability and freshness are observational conclusions.

They do not grant execution authority and do not mutate PAPER state.

## Existing authoritative surfaces

The official PAPER runtime already exposes the evidence required to evaluate
availability and freshness.

Process-level evidence includes the PAPER supervisor process heartbeat.

Operational run evidence includes:

- `desired_state`;
- `observed_state`;
- `worker_claim`;
- `heartbeat_at`;
- `lease_expires_at`;
- `fencing_token`;
- failure evidence;
- terminal state evidence.

The administrator API already exposes the current operational lease timing
without exposing internal worker identity.

## Independent health layers

Process health and operational execution health are separate.

A healthy PAPER supervisor process does not prove that an official PAPER run is
currently healthy or making progress.

Likewise, recently persisted operational state does not prove that the current
supervisor process is alive.

Both layers must be considered independently.

## PAPER supervisor process freshness

The existing production monitoring contract considers the PAPER supervisor
process heartbeat healthy when its age is at most 90 seconds.

The process heartbeat is unhealthy when it is:

- missing;
- invalid;
- timestamped in the future;
- older than 90 seconds.

This threshold is a process-liveness monitoring boundary.

It is not a worker lease duration and must not be used as one.

## Operational ownership freshness

When an operational PAPER epoch is expected to execute, current worker ownership
is represented by its worker claim.

The claim contains:

- the claim timestamp;
- the latest persisted worker heartbeat;
- the lease expiry;
- the fencing token.

For ownership to be considered current at an observation instant:

- a worker claim must exist;
- `heartbeat_at` must not be in the future;
- `lease_expires_at` must be later than the observation instant;
- the claim must belong to the current fenced ownership generation;
- no persisted failure may invalidate the run.

`lease_expires_at` is the authoritative timing boundary for PAPER execution
ownership freshness.

The generic worker-observability stale threshold is not substituted for the
operational PAPER lease expiry.

## Expired ownership

If `lease_expires_at` is at or before the observation instant, the persisted
claim must not be presented as fresh execution ownership.

An expired claim may remain useful historical evidence, but it does not prove
that the worker still owns execution authority.

Monitoring must not extend, renew, replace, or recreate the lease in order to
make the run appear healthy.

## Heartbeat interpretation

`heartbeat_at` is evidence that the owning worker persisted progress or lease
activity at that instant.

A recent heartbeat alone is insufficient to prove current authority if the
lease has already expired.

A heartbeat in the future is invalid freshness evidence.

A heartbeat from a previous fenced ownership generation must not override the
current fencing boundary.

## Desired and observed state

Availability must preserve the distinction between desired state and observed
state.

When `desired_state` and `observed_state` differ, the epoch is converging or
awaiting convergence.

Such a mismatch must not be represented as proof that the requested operational
state has already been achieved.

For example, a desired RUNNING state does not by itself prove that execution is
currently running.

Current execution evidence also requires current observed state and fresh
ownership.

## Intentional inactivity

An intentionally PAUSED or STOPPED official PAPER epoch must not be classified
as unhealthy merely because it has no active worker lease.

Absence of execution ownership is expected when the control plane intentionally
requires no active execution.

Availability therefore must distinguish:

- expected active execution;
- controlled inactivity;
- degraded or stale execution.

Controlled inactivity is not equivalent to runtime failure.

## Failure evidence

Persisted failure evidence takes precedence over an apparently recent process
heartbeat.

A healthy supervisor process does not convert a failed operational epoch into a
healthy one.

Failure recovery must use the normal reviewed administrative control path.

Monitoring and freshness evaluation must not automatically issue START, RESUME,
PAUSE, STOP, replacement-epoch, or lease operations.

## Current-running boundary

An official PAPER epoch may be described as currently running only when the
available evidence supports all relevant conditions:

- execution is desired;
- the observed operational state has converged to running;
- no failure invalidates the epoch;
- a current worker claim exists;
- the worker claim is not expired;
- ownership is consistent with the current fencing generation.

Supervisor process freshness remains an independent health dimension.

A current operational lease does not override a stale or unavailable supervisor
process heartbeat.

## Degraded execution

Execution must be treated as degraded or stale when execution is expected but
the available evidence cannot establish current execution ownership.

Examples include:

- missing claim while execution is expected;
- expired lease;
- invalid or future heartbeat;
- desired and observed state remaining unconverged;
- persisted failure;
- unavailable persistence preventing authoritative observation.

Degraded evidence must not be silently upgraded to healthy status.

## Persistence boundary

Persisted PostgreSQL control-plane evidence is authoritative for operational
PAPER run state.

If that evidence cannot be read, the monitoring layer cannot positively assert
fresh operational execution.

Persistence unavailability is not authority to infer state from process
presence alone.

## Generic worker-observability boundary

ADT also contains a generic worker-observability health contract with its own
staleness threshold.

That threshold serves its existing worker-observability domain.

Phase 9-12 does not redefine the official PAPER lease freshness boundary in
terms of that generic timeout.

For the operational PAPER control plane, lease freshness remains based on the
persisted worker claim and `lease_expires_at`.

## Public projection boundary

Phase 9-12 defines the internal production availability and freshness contract.

It does not introduce a new public endpoint or public ADT Live projection.

Safe public exposure of availability, freshness, health, portfolio, return or
other official-paper evidence belongs to the separate Phase 9 public-projection
delivery.

Administrative evidence that already exists remains administrative.

## Automatic recovery boundary

Freshness evaluation is observational.

It must not automatically:

- start a PAPER epoch;
- pause or resume an epoch;
- stop an epoch;
- claim execution ownership;
- renew a worker lease;
- replace an expired worker;
- create a replacement epoch;
- change a fencing token;
- modify capital authorization;
- create trading orders;
- authorize LIVE trading.

Recovery decisions remain explicit operational actions under the existing
authority model.

## Official PAPER availability model

The official PAPER trader is operationally available only when the evidence
required for its intended current state is authoritative and fresh.

For an actively running epoch, this requires both:

1. supervisor process freshness; and
2. fresh persisted operational execution evidence.

For intentional inactivity, availability means that the control plane
authoritatively reflects the requested inactive state; an active lease is not
required.

The contract therefore does not collapse availability into one heartbeat,
process PID, container state, or timestamp.

## Phase 9-12 decision

Phase 9-12 establishes these boundaries:

- PAPER supervisor process freshness uses the existing 90-second process
  heartbeat boundary;
- process health and operational health remain independent;
- current PAPER execution ownership is based on the persisted worker claim;
- `lease_expires_at` is the operational ownership freshness boundary;
- `heartbeat_at` is supporting freshness evidence, not an independent authority
  grant;
- fencing remains part of current ownership identity;
- desired state alone does not prove observed execution;
- intentional PAUSED or STOPPED state is not failure merely because no worker
  lease exists;
- failure evidence overrides apparently healthy process evidence;
- persistence failure prevents a positive claim of fresh operational execution;
- the generic worker-observability stale timeout is not substituted for PAPER
  lease expiry;
- evaluation remains read-only and grants no execution authority;
- public disclosure remains deferred to the separate public-projection phase.
