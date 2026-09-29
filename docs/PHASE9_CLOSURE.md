# Phase 9 — Production Deployment Closure

## Closure status

Phase 9 — Production Deployment is **COMPLETE / CLOSED LOCALLY**.

This closure means the repository-level production architecture, operational
contracts, safety boundaries and isolated recovery acceptance required by
Phase 9 have been completed and reviewed.

It does **not** mean that ADT has been deployed to a public production host.

No public production deployment, service start/restart, public-domain
activation, TLS certificate issuance, production reverse-proxy activation or
production traffic exposure was performed as part of this closure.

## Phase 9 scope completed

The Phase 9 delivery now contains:

- production container and environment design;
- a Docker Compose production topology;
- separate API, market-supervisor, paper-supervisor and web processes;
- persistent `ADT_DATA_DIR` storage design;
- production environment and secret-management contracts;
- protected CI and release-preparation gates;
- production process supervision and health-check contracts;
- backup and recovery tooling;
- monitoring, alerting and error-tracking contracts;
- TLS, CORS, CSP and rate-limit boundaries;
- performance and cost profiling;
- official PAPER availability and freshness semantics;
- a bounded read-only official PAPER public projection.

Persistent trading execution remains outside HTTP request handling.

## Supabase and migrations

The linked Supabase migration history was reviewed and synchronized through the
Phase 8/Phase 9 migration boundary already recorded in the roadmap.

The final reviewed `db push --dry-run` evidence reports the remote database up
to date.

This migration evidence does not imply that Phase 9 performed a new production
deployment or granted new trading authority.

## Backup and recovery acceptance

Phase 9 required an actual restore test before backup/restore work could be
considered accepted.

That requirement has now been satisfied in both persistence domains covered by
the ADT recovery contract.

### Filesystem acceptance

An isolated synthetic filesystem recovery set was created under `/tmp` using
the production filesystem backup script.

Acceptance verified:

- canonical `data/` archive root;
- backup checksum validation;
- recovery-set identity validation;
- bounded archive members;
- actual extraction through the production restore script;
- restored-content equality by SHA-256 manifest;
- restored root ownership `10001:10001`;
- explicit `PROMOTION NOT PERFORMED`;
- cleanup of the temporary recovery tree.

The test did not modify `/srv/adt/data`.

### PostgreSQL acceptance

An ephemeral PostgreSQL 18 cluster was initialized under `/tmp` and configured
for Unix-socket-only access.

A synthetic source database was created and backed up through
`ops/backup-production-postgres.sh`.

The backup was restored into a separate empty target database using the
documented portable restore order:

1. `pre-data.sql`;
2. `data.dump` through `pg_restore`;
3. `post-data.sql`.

Acceptance verified:

- backup checksums;
- exact restored row equality;
- identity/sequence state equality;
- correct next generated identity value;
- primary/unique/check/foreign-key constraint restoration;
- active foreign-key enforcement;
- active check-constraint enforcement.

The ephemeral PostgreSQL cluster was stopped and removed after the test.

The system PostgreSQL `18/main` cluster remained down throughout the
acceptance.

No Supabase URL, credential or remote database connection was used.

## Backup scope boundary

The ADT PostgreSQL logical backup covers ADT-owned state in the PostgreSQL
`public` schema.

It is not represented as a complete backup of Supabase-managed platform state
such as authentication, storage, realtime/platform schemas, managed roles or
provider-owned migration infrastructure.

Managed Supabase backup/PITR remains a separate platform recovery concern.

## Official PAPER availability

Phase 9 defines process health separately from operational execution health.

Positive operational ownership depends on persisted authoritative evidence,
including the active worker claim, heartbeat/freshness evidence,
`lease_expires_at`, fencing identity and failure state.

Desired state alone is not evidence that official PAPER execution is active.

Intentional PAUSED or STOPPED state is not classified as a failure merely
because no active worker lease exists.

## Public projection

The public official PAPER projection is exposed through:

`GET /api/v1/public/official-paper`

The projection is:

- read-only;
- bounded;
- derived from persisted operational PAPER evidence;
- sanitized to avoid leaking internal worker/fencing authority;
- separate from the inherited Phase 5 public PAPER runner routes.

The endpoint grants no START, STOP, PAUSE, RESUME, worker-claim, capital,
order or LIVE authority.

Phase 9 does not claim that the aggregate ADT Official Portfolio, cumulative
portfolio performance, unified equity history or ADT Confidence Score public
product is implemented. Those remain Phase 10 concerns.

## Kubernetes decision

The roadmap requires Kubernetes only if measured scale demonstrates a need.

Current Phase 9 evidence does not establish such a need.

**Decision: DEFERRED.**

The supported production topology remains the reviewed single-host Docker
Compose architecture unless future measured scale justifies a different
orchestration layer.

## Operational deployment boundary

Repository-level Phase 9 closure is distinct from an actual hosted production
activation.

This closure does not assert:

- that Docker production containers were started on a production host;
- that a public DNS name is configured;
- that production TLS certificates are issued;
- that public ingress is active;
- that external monitoring vendors are configured;
- that a production host is currently serving ADT traffic.

Those actions require explicit operational authorization and environment-
specific acceptance evidence.

## Trading authority boundary

Nothing in Phase 9 grants or expands:

- LIVE execution authority;
- real-capital authorization;
- exchange credential authority;
- automatic capital approval;
- recommendation-driven activation;
- implicit PAPER START/RESUME;
- HTTP-triggered execution authority.

The continuously available trader defined by Phase 9 remains official PAPER
only.

## Closure result

All twelve Phase 9 roadmap deliverables are implemented, validated locally or,
for the conditional Kubernetes item, resolved by an explicit reviewed deferral.

Filesystem and PostgreSQL recovery acceptance both passed in isolated,
non-production environments.

Phase 9 is therefore **COMPLETE / CLOSED LOCALLY**.

Actual public production deployment remains a separately authorized operational
action and is not claimed by this closure.
