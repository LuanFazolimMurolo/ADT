# Production Performance and Cost Profiling

## Scope

This document defines the Phase 9 production performance and cost profiling
contract for ADT.

The objective is to establish measurable production capacity and cost evidence
before selecting infrastructure sizing, scaling mechanisms, or hard container
resource limits.

This phase does not invent CPU, memory, storage, latency, throughput, or cost
thresholds without measured evidence.

## Production topology baseline

The initial ADT production topology remains:

- one production host;
- managed Supabase/PostgreSQL;
- one persistent POSIX `ADT_DATA_DIR`;
- one API process boundary;
- one market-data supervisor process boundary;
- one PAPER supervisor process boundary;
- one static frontend container;
- one public HTTP/TLS edge.

The API does not own persistent market-data or PAPER execution loops.

Kubernetes is not part of the initial topology.

It may be considered only if measured scale or availability requirements
justify it.

## Existing bounded application controls

ADT already has application-level controls that limit several potential
resource-amplification paths.

Examples include:

- bounded database connection pools;
- bounded supervisor discovery pages;
- bounded supervisor polling cadence;
- bounded market-data HTTP connection count;
- bounded market-data request timeout;
- bounded upstream retry count;
- bounded upstream retry-after handling;
- bounded market-data fetch sizes;
- API pagination and response-size boundaries;
- frontend bundle budgets;
- request-body size boundaries.

These controls are part of application correctness and must not be replaced by
arbitrary infrastructure limits.

## Profiling dimensions

### API

Observe:

- CPU utilization;
- resident memory;
- request throughput;
- response latency;
- database-pool utilization;
- database wait or timeout evidence;
- HTTP error rate;
- restart frequency.

### Market-data supervisor

Observe:

- CPU utilization;
- resident memory;
- polling duration;
- active operation count;
- upstream request volume;
- upstream retry and rate-limit evidence;
- database-pool utilization;
- filesystem growth attributable to market data;
- process restart frequency.

### PAPER supervisor

Observe:

- CPU utilization;
- resident memory;
- polling duration;
- concurrent active PAPER epochs;
- PAPER cycle duration;
- database-pool utilization;
- persisted artifact growth;
- process restart frequency.

### Static web

Observe:

- container CPU utilization;
- resident memory;
- emitted frontend bundle size;
- request volume at the public edge;
- static-response latency where edge measurements are available.

## Host capacity evidence

Before a production host size is treated as sufficient, profiling should record:

- host CPU capacity;
- host memory capacity;
- filesystem capacity;
- filesystem utilization;
- filesystem growth rate;
- container/process CPU utilization;
- container/process memory utilization;
- sustained and peak utilization windows.

A single instantaneous sample is not enough to establish production capacity.

Capacity decisions must be based on representative workload observations.

## Database cost and capacity

Managed PostgreSQL is an external production dependency.

Profiling must distinguish application-host resource usage from database
resource usage.

Relevant evidence includes:

- configured connection-pool bounds;
- active and waiting connections where available;
- query latency;
- slow-query evidence;
- database storage growth;
- provider usage or cost information when a production plan is selected.

Increasing a connection pool is not automatically a performance improvement.

Any pool-size change requires evidence that database connection capacity and
query behavior support the change.

## Filesystem growth

`ADT_DATA_DIR` contains persistent application artifacts and therefore has a
direct storage-cost dimension.

Profiling should measure growth attributable to:

- raw market data;
- derived market data;
- snapshots;
- backtest artifacts;
- optimization artifacts;
- ML artifacts;
- PAPER artifacts.

Phase 9 does not invent an artifact-retention policy.

Retention remains a separate reviewed decision.

Until retention is defined, storage growth must remain observable rather than
being silently deleted to satisfy a capacity target.

## Network and upstream cost

Public market-data traffic is bounded by existing HTTP client configuration.

Profiling should distinguish:

- normal successful requests;
- retries caused by network failures;
- retries caused by upstream server failures;
- upstream rate-limit backoff;
- total request volume;
- transferred data where measurable.

Retry traffic must not be interpreted as useful throughput.

Repeated upstream throttling is evidence for workload review, not authority to
increase request concurrency automatically.

## Frontend performance

The existing deterministic frontend bundle budget remains the build-time
performance guard.

Phase 9-11 does not replace that budget with an unmeasured target.

Production profiling may later add public-edge latency and transfer-size
evidence when a concrete edge and production domain exist.

## Cost model

Production cost must be decomposed by resource instead of represented as one
unexplained monthly number.

Relevant cost drivers include:

- production host;
- managed PostgreSQL;
- persistent filesystem capacity;
- backup storage;
- network transfer where billed;
- public HTTP/TLS edge where billed;
- optional future observability services.

No provider price is recorded as an ADT production baseline until that provider
and plan are explicitly selected.

## Container resource limits

The current production Compose topology does not define explicit CPU or memory
limits.

Phase 9-11 does not invent those limits without profiling evidence.

This is intentional.

A future CPU or memory limit must be based on measured workload behavior and
must leave sufficient margin for normal bursts, graceful shutdown, recovery,
and health checks.

A resource limit must not be introduced merely to make an infrastructure cost
target appear satisfied.

## Scaling decision

The default scaling model remains the reviewed single-host topology.

Vertical or horizontal scaling requires measured evidence.

Signals that may justify a scaling review include sustained resource pressure,
persistent latency degradation, exhausted database capacity, storage pressure,
or availability requirements that cannot be satisfied by the current topology.

These signals initiate human review.

They do not automatically:

- create replicas;
- change database pool sizes;
- provision infrastructure;
- migrate to Kubernetes;
- restart services;
- alter PAPER execution;
- change capital authorization;
- authorize LIVE trading.

## Kubernetes boundary

Kubernetes remains explicitly deferred.

It may be reconsidered only if measured scale or availability requirements
justify the additional operational complexity and cost.

The mere existence of multiple ADT process boundaries is not evidence that
Kubernetes is required.

## Performance acceptance model

Phase 9 deployment preparation requires evidence that performance-sensitive
paths are bounded and profileable.

It does not require invented production SLOs before representative production
measurements exist.

Production-measured SLOs may be defined later from observed behavior.

Any future latency, throughput, CPU, memory, storage-growth or cost threshold
must identify:

- the metric;
- the measurement source;
- the observation window;
- the workload context;
- the threshold rationale;
- the operator response.

## Authority boundary

Performance and cost profiling is observational infrastructure.

Profiling and capacity controls must not:

- issue START, PAUSE, RESUME, or STOP commands;
- create or renew trading leases;
- activate PAPER sessions;
- create capital authorization;
- execute PAPER cycles;
- create trading orders;
- authorize LIVE trading.

Performance evidence may inform infrastructure decisions only.

Trading and operational authority remain governed by their existing contracts.

## Phase 9-11 decision

Phase 9-11 establishes the performance and cost profiling boundary without
inventing infrastructure sizing.

The current evidence supports:

- retaining bounded application-level controls;
- retaining the deterministic frontend bundle budget;
- measuring the single-host topology before introducing hard resource limits;
- keeping retention policy separate;
- keeping production-measured SLOs separate;
- keeping external observability optional;
- keeping Kubernetes deferred until measured scale requires it.

Concrete infrastructure sizing remains a deployment decision based on measured
production evidence.
