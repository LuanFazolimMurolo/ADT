# ADT Production Monitoring and Alert Contract

## Purpose

This document defines the production monitoring and human-response contract
for ADT.

Monitoring is observational only.

No healthcheck, log consumer, alert rule, dashboard, or error-tracking
integration may:

- issue START, PAUSE, RESUME, or STOP commands;
- create or renew PAPER execution authority;
- create or renew capital authorization;
- acquire or extend worker leases;
- execute trading cycles;
- mutate trading state in response to an alert.

Operational state remains owned by the existing ADT domain and persistence
contracts.

## Monitoring layers

ADT distinguishes three monitoring layers.

### 1. Process health

Process health answers:

> Is the deployed process alive and continuing to make event-loop progress?

Current process-health surfaces:

- API: `/health/readiness`
- market supervisor:
  `/tmp/adt-runtime-health/market-supervisor.json`
- PAPER supervisor:
  `/tmp/adt-runtime-health/paper-supervisor.json`

Supervisor heartbeat files are ephemeral container-local state.

They are not:

- trading state;
- worker state;
- lease state;
- backup material;
- recovery evidence;
- execution authority.

Supervisor process heartbeat interval:

- 30 seconds

Supervisor process heartbeat stale threshold:

- 90 seconds

### 2. Operational health

Operational health answers:

> Is the worker/domain state healthy according to persisted ADT contracts?

This includes persisted lifecycle, heartbeat, failure, lease, and fencing
state already owned by the corresponding domain.

Existing worker-observability presentation states include:

- `HEALTHY`
- `STALE`
- `STOPPED`
- `FAILED`

The existing worker-runtime stale threshold is:

- 120 seconds

Operational health must not be inferred only from process health.

A process may be alive while its worker/domain state is degraded.

Likewise, persisted worker state does not by itself prove that the owning
supervisor process is currently alive.

### 3. Diagnostic telemetry

Structured logs provide diagnostic evidence.

Logs are not the source of truth for operational control state.

Current supervisor lifecycle events:

- `supervisor_starting`
- `supervisor_stopped`

Current supervisor degradation event:

- `persistence_retry`

Structured production logs intentionally expose only a narrow allowlist of
fields.

Sensitive arbitrary payloads, database URLs, secrets, tokens, SQL, exception
details, trading payloads, and unrestricted object serialization must not be
added to the production logging contract.

## Alert severity model

### P1 — Critical

A P1 condition means the official PAPER service cannot be trusted to remain
continuously available or a core production dependency is unavailable.

Current P1 conditions:

#### API readiness continuously unhealthy

Signal:

- production API container remains unhealthy after its configured Docker
  healthcheck retries.

Interpretation:

- API process and/or PostgreSQL readiness is unavailable.

Operator response:

1. inspect container health;
2. inspect sanitized application logs;
3. verify PostgreSQL/Supabase availability;
4. verify configuration and credentials exist without printing secret values;
5. do not bypass readiness;
6. do not manually synthesize operational trading state.

#### PAPER supervisor continuously unhealthy

Signal:

- `paper-supervisor` Docker health becomes unhealthy because the process
  heartbeat is unavailable, invalid, from the future, or older than
  90 seconds.

Interpretation:

- the persistent PAPER supervisor process is not proving event-loop liveness.

Operator response:

1. inspect container status and sanitized logs;
2. identify whether the process exited, is restarting, or is stalled;
3. inspect persisted PAPER operational state separately;
4. do not assume an old persisted lease proves process liveness;
5. do not create, renew, or modify PAPER authority as part of monitoring;
6. recovery actions require the normal operational/deployment procedure.

#### Market supervisor continuously unhealthy

Signal:

- `market-supervisor` Docker health becomes unhealthy because the process
  heartbeat is unavailable, invalid, from the future, or older than
  90 seconds.

Interpretation:

- the persistent market-data supervisor process is not proving event-loop
  liveness.

Operator response:

1. inspect container status and sanitized logs;
2. inspect persisted collector state separately;
3. verify PostgreSQL and upstream market-data connectivity as independent
   dependencies;
4. do not synthesize collector START/STOP state from the monitoring layer.

### P2 — High

A P2 condition indicates persisted operational degradation requiring timely
human inspection.

#### Worker/runtime `FAILED`

Signal:

- persisted operational state reports `FAILED`;
- a sanitized `failure_code` is available from the owning domain.

Interpretation:

- the domain has explicitly terminalized the runtime as failed.

Operator response:

1. record the failure code;
2. inspect domain-specific persisted state;
3. inspect sanitized logs around the failure time;
4. determine whether the failure is authority, configuration, lease, local
   state, database, upstream, or internal;
5. use the normal administrative control path for any subsequent action.

Monitoring must never automatically convert `FAILED` into START, RESUME,
STOP, or a replacement execution epoch.

#### Worker/runtime `STALE`

Signal:

- the existing worker-observability contract derives `STALE`;
- current stale threshold: 120 seconds.

Interpretation:

- persisted worker heartbeat is too old for the existing operational
  freshness contract.

Operator response:

1. compare process health with operational health;
2. inspect persisted lifecycle and heartbeat timestamps;
3. inspect sanitized supervisor logs;
4. determine whether the owning process is alive but unable to progress;
5. do not mutate the worker lease from the monitoring path.

### P3 — Warning

A P3 condition indicates degradation that may recover automatically but should
remain visible to operators.

#### Persistence retry

Signal:

structured warning event:

- `event=persistence_retry`

Current components:

- `market-supervisor`
- `paper-supervisor`

Current operations:

- market: `operation=poll_once`
- PAPER: `operation=list_nonterminal`

Interpretation:

- the supervisor observed transient persistence unavailability and entered its
  existing bounded retry path.

A single `persistence_retry` event is not, by itself, a P1 incident.

Escalation should be based on continued degradation, such as:

- repeated retries over an operator-defined observation window;
- API readiness becoming unhealthy;
- supervisor process health becoming unhealthy;
- worker state becoming `STALE` or `FAILED`.

No automatic retry counter or escalation threshold is defined in this phase.

A future external alerting integration may define an observation window only
after production behavior has been measured.

## Health matrix

| Process / state | Signal | Healthy condition | Degraded condition |
| --- | --- | --- | --- |
| API | `/health/readiness` | HTTP success | Docker health unhealthy |
| Market supervisor process | process heartbeat | age <= 90 s | missing, invalid, future, or age > 90 s |
| PAPER supervisor process | process heartbeat | age <= 90 s | missing, invalid, future, or age > 90 s |
| Worker operational runtime | persisted worker state | `HEALTHY` | `STALE` or `FAILED` |
| Persistence dependency | structured logs | no active retry evidence | `persistence_retry` observed |

## Correlation guidance

Process health and operational health must be evaluated independently.

### Process healthy + worker healthy

Expected normal condition.

### Process unhealthy + persisted worker apparently healthy

Treat process health as degraded.

A recently persisted heartbeat may be residual state and does not prove that
the supervisor process is currently alive.

### Process healthy + worker stale

Treat operational health as degraded.

The process is alive but the worker/domain is not proving expected progress.

### Process healthy + worker failed

Treat the domain failure as authoritative.

Process liveness does not override a persisted `FAILED` state.

### Persistence retries + process healthy

Treat as a warning while retry behavior remains bounded and process health is
fresh.

Escalate only when the persistence degradation persists or affects readiness
or operational freshness.

## Docker healthcheck behavior

Current supervisor Docker healthchecks are read-only.

They execute the heartbeat validation CLI and must not:

- write a heartbeat;
- start a supervisor;
- restart a container;
- renew a lease;
- modify PostgreSQL;
- modify PAPER state;
- perform market-data collection.

Container restart policy and monitoring are separate concerns.

`restart: unless-stopped` is not an alerting mechanism.

## Error tracking boundary

ADT does not currently require an external error-tracking dependency.

Any future external integration must:

- consume diagnostic information only;
- preserve the existing structured-log redaction boundary;
- avoid arbitrary exception payload capture by default;
- never receive database credentials or secrets;
- never become an operational control authority;
- never execute trading or administrative actions.

External error tracking is optional observability infrastructure, not a
prerequisite for ADT correctness.

## Alert delivery boundary

This phase defines alert conditions but does not configure an external alert
delivery provider.

Examples of possible future delivery mechanisms include:

- infrastructure monitoring;
- Docker/host monitoring;
- log aggregation;
- error tracking;
- notification services.

Selecting a provider is a separate deployment decision.

No new dependency or external service is introduced by this contract.

## Operator safety rules

During an alert investigation:

1. preserve persisted operational evidence;
2. inspect health before taking recovery action;
3. do not expose secrets in terminal output or tickets;
4. do not modify database state to make a healthcheck pass;
5. do not manually forge heartbeats;
6. do not equate monitoring acknowledgement with operational recovery;
7. do not treat ML recommendation approval as capital authorization;
8. do not enable LIVE trading;
9. follow the existing deployment and recovery contracts for service
   restoration.

## Current scope

This monitoring contract covers:

- API readiness;
- market supervisor process liveness;
- PAPER supervisor process liveness;
- persisted worker health;
- structured lifecycle logs;
- structured persistence retry logs.

It does not yet define:

- production-measured SLOs;
- production-measured RTO/RPO;
- automatic paging thresholds for repeated persistence retries;
- third-party monitoring vendors;
- automatic remediation;
- LIVE trading monitoring.

Those require separate evidence or authorization.

## Phase 9-09 external observability decision

Phase 9-09 does not introduce a third-party monitoring or error-tracking
provider.

This is intentional.

The current production contract already exposes the required local
observability surfaces:

- API liveness/readiness;
- Docker container health;
- market-supervisor process heartbeat;
- PAPER-supervisor process heartbeat;
- persisted worker lifecycle and freshness;
- sanitized structured lifecycle logs;
- sanitized structured persistence-retry logs;
- documented alert conditions and operator response.

The production-topology decision explicitly deferred selection of a monitoring
vendor.

No existing ADT contract requires Sentry, Prometheus, Grafana,
OpenTelemetry, Datadog, New Relic, Rollbar, or another equivalent provider as
a prerequisite for correctness or deployment preparation.

A future observability provider may be introduced only when there is a
separate operational need and reviewed deployment decision.

Such an integration must remain observational and must preserve all existing
authority, redaction, and secret-handling boundaries.

Phase 9-09 therefore closes with no new external observability dependency.
