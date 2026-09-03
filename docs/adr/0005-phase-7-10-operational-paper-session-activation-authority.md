# ADR 0005 — Phase 7-10 Operational Paper Session Activation Authority

## Status

Accepted — Gate 1

## Date

2026-09-03

## Scope

Phase 7-10 — Operational Paper Session Activation Authority Foundation.

This ADR defines an administrative eligibility authority between one exact
`OperationalPaperSessionMaterialization` and any future paper-runner control
authority. It does not design or operate the runner process.

## Context

Phase 7-07 created approved, frozen operational paper-session profiles. Phase
7-08 created historical capital-authorization grants backed by the existing
Phase 1 simulated-capital ledger. Phase 7-09 created durable materialization
provenance and published the exact immutable local `PaperSessionConfig` with a
deterministic `config_checksum` and `session_id`.

A `MATERIALIZED` record proves what was published. It deliberately does not
authorize execution. The existing CLI and `PaperTradingContinuousRunner` can
receive fixed session IDs and execute work, but they have no durable
administrator activation authority, runtime subscription contract or dynamic
control-plane lifecycle.

Phase 7-10 must answer one bounded question: which durable administrative
record authorizes one exact materialization to be considered eligible for a
future execution attempt? Starting or controlling an execution process remains
a separate question for a later delivery.

## Decision

Phase 7-10 introduces the conceptual PostgreSQL aggregate
`OperationalPaperSessionActivation` as a historical activation grant.

Each aggregate authorizes one exact materialization. Its lifecycle is
one-way, `AUTHORIZED -> REVOKED`. Revocation is terminal for that activation
identity. A later reauthorization creates a new activation identity, and at
most one activation may currently be `AUTHORIZED` for a materialization.

An `AUTHORIZED` activation is necessary but never sufficient by itself for a
future start. Effective eligibility is a current conjunction of the activation
grant, all required upstream authorities, the exact materialization/config
binding and the resolvable frozen strategy plugin.

## Authority model

The accepted authority chain is:

```text
OperationalMandate
    -> OperationalPaperSessionProfile
    -> OperationalPaperCapitalAuthorization
    -> OperationalPaperSessionMaterialization
    -> immutable local PaperSessionConfig
    -> OperationalPaperSessionActivation
    -> future runner-control authority
    -> paper execution
```

PostgreSQL owns administrative identities, lifecycle, actors, provenance and
current activation authority. The paper filesystem remains authoritative for
canonical executable config bytes, deterministic config/session identities,
execution state, replay artifacts and filesystem locking. The filesystem does
not become activation authority. The Phase 1 ledger remains authoritative for
gross simulated capital; this delivery creates no second ledger.

## Models evaluated

| Model | Benefits | Costs and risks | Decision |
| --- | --- | --- | --- |
| A — one mutable activation per materialization | Simple point lookup and one stable row | `REVOKED -> AUTHORIZED` reuses identity, obscures authorization intervals, weakens actor history and creates an ABA shape for future runner epochs and stale clients | Rejected |
| B — historical activation grants | Preserves each authorization and revocation, gives reauthorization a new identity, supports exact retries and gives a future runtime epoch an unambiguous grant to bind | Requires history plus a current-grant uniqueness invariant | Selected |
| C — runtime desired-state aggregate | Could eventually represent start/pause/resume/stop in one place | Prematurely combines administrative eligibility with process intent, leases, fencing, heartbeat and supervision; exceeds the bounded 7-10 scope | Rejected |

Model B is superior here because the additional historical row is a small
persistence cost and directly improves auditability, reauthorization clarity,
idempotency and ABA prevention. It also leaves the future runtime aggregate
free to have its own identity and lifecycle.

## Selected aggregate model

`OperationalPaperSessionActivation` is the aggregate name. Each aggregate is
one historical grant, not a mutable singleton and not a runtime session.

The current activation for a materialization is the unique grant, if any, in
`AUTHORIZED`. Revoked grants remain queryable and immutable except for the one
accepted terminal transition. No deletion is part of the lifecycle.

## Identity model

The conceptual aggregate carries or binds:

- a backend/PostgreSQL-generated stable UUID `activation_id`;
- its exact `materialization_id` and `materialization_checksum`;
- the exact `authorization_id` and `authorization_checksum` copied from the
  materialization chain;
- exact `profile_id`, approved revision and profile specification checksum;
- exact mandate ID, approved revision and mandate specification checksum;
- exact `simulation_id`;
- exact deterministic local `session_id` and `config_checksum`;
- activation schema/contract versions and canonical `activation_checksum`;
- activation and revocation actors/timestamps;
- an actor-scoped create-idempotency identity and versioned intent
  fingerprint; and
- positive optimistic-concurrency `record_version`.

The copied bindings are self-contained audit evidence and must equal the exact
immutable materialization record; they do not create replacement authorities.
`activation_id`, `materialization_id`, `authorization_id` and `session_id` are
distinct identities. `session_id` remains a deterministic content identity,
never an operational UUID. In particular, `activation_id != session_id`.

## Canonical activation checksum

`activation_checksum` is the canonical lowercase SHA-256 of the immutable
semantics of one activation grant. It lets later consumers bind to exactly what
was authorized without treating mutable lifecycle or audit metadata as grant
content.

Its versioned canonical input includes every immutable, identity-bearing grant
binding:

- activation schema version and activation contract version;
- exact `materialization_id` and `materialization_checksum`;
- exact capital `authorization_id`, `authorization_checksum` and frozen
  authorization binding represented by the materialization;
- exact `profile_id`, approved revision and profile specification checksum;
- exact mandate ID, approved revision and mandate specification checksum;
- exact `simulation_id`;
- exact deterministic `session_id`; and
- exact deterministic `config_checksum`.

It explicitly excludes:

- `activation_id`;
- lifecycle state, including `AUTHORIZED` and `REVOKED`;
- `record_version`;
- authorization actor and revocation actor;
- creation/authorization timestamp and revocation timestamp;
- idempotency key and `create_intent_fingerprint`;
- any future runtime state;
- heartbeat, lease or process identity; and
- mutable current-authority state.

The checksum answers "which exact activation grant was authorized?", not
"what is the aggregate's current state?" It is not a checksum of the complete
audit record or mutable lifecycle.

## Lifecycle

The lifecycle is:

```text
AUTHORIZED -> REVOKED
REVOKED    -> no transition
```

Creation starts at `record_version = 1`. Revocation requires the expected
record version, records its actor and time, and increments the version once.
Authority bindings and activation checksum never change. Revocation remains
available even if an upstream authority is already invalid, because removing
authority must not depend on that upstream authority still being valid.

Revocation therefore does not change `activation_checksum`. It changes the
lifecycle and revocation audit metadata, increments `record_version` under the
later persistence contract, and preserves the grant's historical semantic
identity.

## Activation vs runtime execution

Activation is an administrator's durable authorization that an exact
materialization may be considered eligible by a later runner-control command.
Start is a future operational intent to establish a runtime epoch and cause a
runner process to execute.

The distinction is categorical:

```text
ACTIVATED != RUNNING
activation != start intent
activation state AUTHORIZED != runner state RUNNING
activation authorization != start intent
```

The first two lines are human shorthand. The aggregate's formal lifecycle
state is `AUTHORIZED`, never `ACTIVATED`; runner state belongs to a future
runtime authority.

An activation neither invokes `run_once` nor starts
`PaperTradingContinuousRunner`. It does not create a process, worker claim,
lease, heartbeat, runner state or claim that anything is online. It also does
not provide pause, resume or stop semantics.

## Current-authority revalidation

A new activation is accepted only after the backend resolves the exact
materialization, verifies its local config read-only, and transactionally
revalidates every current PostgreSQL authority. A future start must repeat the
current checks; historical success at activation time is not a capability
token that bypasses later changes.

| Authority or condition | At activation | At future start | Rationale |
| --- | --- | --- | --- |
| Materialization is exact and `MATERIALIZED` | REQUIRED | REQUIRED | PREPARED permits reconciliation only; it grants no execution eligibility |
| Capital authorization is exact and `AUTHORIZED` | REQUIRED | REQUIRED | Revoked capital cannot support either new activation or execution |
| Bound profile is exact and `APPROVED` | REQUIRED | REQUIRED | Archived profile removes current operational authority |
| Bound mandate is exact and `APPROVED` | REQUIRED | REQUIRED | The mandate remains the instrument/market authorization authority |
| Bound simulation is `ACTIVE` | REQUIRED | REQUIRED | A terminal simulation cannot authorize new paper execution |
| Materialization, config checksum and session ID agree | REQUIRED | REQUIRED | Prevents substitution across PostgreSQL and filesystem identities |
| Local config exists, is canonical and equals the materialization | REQUIRED | REQUIRED | Activation checks present evidence; start closes the distributed-boundary gap |
| Quote asset, simulation currency and exact capital agree | REQUIRED | REQUIRED | Preserves the financial binding; no float conversion or second balance is allowed |
| Frozen snapshot's exact plugin identity is registered/resolvable | REQUIRED | REQUIRED | Eligibility cannot promise an executor that this backend cannot resolve |
| Latest mutable strategy-definition payload equals the snapshot | NOT REQUIRED | NOT REQUIRED | The approved frozen snapshot is historical authority, not a pointer to latest mutable content |
| Required RAW input is present and fit for the requested run | NOT REQUIRED | REQUIRED | Data readiness belongs to the future execution attempt, not administrative activation |
| Activation itself is currently `AUTHORIZED` and exact | NOT APPLICABLE | REQUIRED | The grant is created by the activation operation and consumed by future start |

Thus the first nine positive conditions are `REQUIRED AT BOTH`; RAW readiness
is `REQUIRED ONLY AT FUTURE START`; mutable-definition equality is
`NOT REQUIRED`.

## Later upstream authority changes

Later capital revocation, profile archival, mandate archival or simulation
terminalization does not automatically update or delete an activation. The
activation remains immutable historical evidence of the decision that was
valid at its linearization point.

It immediately ceases to be effectively eligible, however, because future
start resolves current authority as a conjunction and must fail closed. The
same rule applies if the local config is missing/corrupt or the exact frozen
plugin can no longer be resolved. An old activation can never bypass a later
upstream revocation.

Explicit activation revocation is different: it directly and permanently
removes current authority from that grant, regardless of upstream state.

Automatic cascade revocation is rejected. It would introduce hidden
cross-aggregate writes, lose the distinction between who revoked which
authority, complicate lock ordering and still would not eliminate mandatory
future-start revalidation.

## Re-activation semantics

A `REVOKED` activation never returns to `AUTHORIZED`. Reauthorization requires
a new administrator intent, new idempotency key and new backend-generated
`activation_id`. The new grant may bind the same still-exact materialization
only after all current activation conditions pass again.

This terminal rule preserves authorization intervals and actor audit, prevents
identity-level ABA, and lets a future runtime epoch bind the precise grant that
authorized it. The uniqueness invariant allows the new grant only after no
older grant remains `AUTHORIZED`.

## Idempotency

Create uses a non-sensitive actor-scoped idempotency key and a versioned
deterministic intent fingerprint. The fingerprint covers the exact
materialization intent and activation contract version; persisted bindings are
resolved from the immutable materialization rather than trusted from a browser.

`create_intent_fingerprint` and `activation_checksum` are distinct. The
fingerprint identifies an administrator's submitted intent so an ambiguous
retry can be recognized. The checksum identifies the immutable, server-resolved
semantics of the persisted grant. The idempotency key is never part of the
checksum, and `activation_id` is generated independently rather than derived
from either value.

After sufficient canonical request validation, an existing
actor/key/fingerprint is resolved before mutable authority, plugin and
filesystem revalidation. It returns the originally committed activation,
including if that historical grant is now `REVOKED` or no longer effectively
eligible. This is retry recognition, not renewed authority.

Reusing the same actor/key with a different fingerprint conflicts. Concurrent
new keys are distinct intents, not retries. A new authorization after
revocation must use a new key; replaying the old key returns the old revoked
grant and cannot create another activation. A future browser supplies intent
and idempotency identity, never an arbitrary `activation_id`.

## Concurrency / linearization

For a genuinely new grant, one bounded PostgreSQL transaction locks and
revalidates the current chain in a canonical order compatible with the existing
financial mutex: simulation, capital authorization, profile, mandate, exact
materialization, then the activation/current-grant invariant. Gate 2B must
confirm the final order against every existing mutation path before encoding
it. Plugin resolution and filesystem I/O are not performed while PostgreSQL
row locks are held.

The linearization point is the commit that, under those locks and uniqueness
defenses, inserts the new `AUTHORIZED` activation. A partial/current-state
uniqueness invariant must permit at most one `AUTHORIZED` activation per
materialization. Database constraints and trigger/predicate defenses remain
authoritative even when service validation already passed.

Two different new intents racing for the same materialization cannot both
commit: one wins and the other conflicts and must reload. Two revocations use
the expected `record_version`; at most one transition wins. Revoke racing with
reauthorization is ordered by the current-grant row and uniqueness invariant;
reauthorization can commit only after revocation commits and only as a new
intent.

Upstream mutations use the shared canonical locks. If an upstream mutation
linearizes first, activation fails revalidation. If activation linearizes
first, the later mutation remains legal under its own rules but removes
effective future eligibility without rewriting activation history.

## Filesystem verification boundary

The supported paper repository treats `config.json` as immutable and
content-addressed, but PostgreSQL cannot atomically lock or commit that file.
Activation creation therefore uses this distributed sequence:

1. resolve the materialization and immutable expected identities;
2. read the existing config through a bounded, safe paper-repository path;
3. decode and validate it, verify canonical bytes, recompute
   `config_checksum` and `session_id`, and require exact equality with the
   materialization;
4. resolve the exact frozen plugin without dynamic import;
5. open the bounded PostgreSQL transaction, lock/revalidate current authority,
   insert or resolve the activation, and commit; and
6. require a future start to repeat both filesystem and current-authority
   validation before establishing runtime authority.

The read performs no config creation, repair, state publication or other paper
filesystem mutation. PostgreSQL locks are not held during filesystem I/O.

This order has an acknowledged TOCTOU window. Supported application paths do
not mutate an existing canonical config, so ordinary concurrent publication
can only converge on identical bytes or fail closed. Unsupported deletion or
corruption after the read cannot be made atomic with the database commit. It
does not rewrite the historical grant; instead, future-start verification
detects the loss and refuses execution. Activation therefore proves successful
verification at its decision time, not continuous file availability.

## Strategy snapshot semantics

The exact approved/materialized frozen strategy snapshot remains the historical
strategy authority. Activation and future start must resolve its recorded
plugin name/version and supported schema/lifecycle through the explicit
server-side registry. Arbitrary imports remain forbidden.

Neither operation silently substitutes the latest mutable strategy definition,
nor does a later mutable revision/archive invalidate the frozen snapshot merely
because its current payload differs. Resolvability of the exact frozen executor
is a current capability check; equality with latest mutable content is not.

## Future runner-control contract

A future runner-control delivery may consume from 7-10:

- the one current `AUTHORIZED` activation identity, checksum, actor and
  provenance;
- its exact `MATERIALIZED` materialization and complete upstream bindings;
- exact deterministic `session_id` and `config_checksum`;
- the verified canonical local `PaperSessionConfig`; and
- a fresh eligibility result obtained by revalidating current upstream
  authority, filesystem identity and plugin resolvability.

The future delivery must still define its own start intent, runtime epoch,
desired state, process lease, heartbeat, supervisor/worker claim, fencing and
pause/resume/stop behavior. None is implicit in an activation, and no runtime
consumer may rely only on a previously cached eligibility result.

## PostgreSQL persistence direction

`EXPECTED MIGRATION = YES` for a later implementation gate, but no migration
is created by Gate 1A.

The expected persistence foundation must durably represent historical
activation identities and exact bindings, lifecycle and version defenses,
activation/revocation actors and timestamps, checksum semantics, actor-scoped
create idempotency, optimistic concurrency, current-grant uniqueness and
deterministic bounded queries. It must preserve referenced historical records
and forbid authority deletion or binding mutation.

RLS must be enabled. Browser/Data API roles, including `anon`, `authenticated`
and `service_role`, receive no direct activation authority. Administrative
access remains through authenticated, administrator-authorized FastAPI backed
by the direct PostgreSQL connection. Applying any migration to remote Supabase
is a separate controlled operation outside this gate.

## Security and trust boundary

The backend owns actor identity, activation UUID generation, current-authority
resolution, checksums and lifecycle transitions. Browser claims about state,
bindings, eligibility, available capital or filesystem content are never
authoritative. No secret or privileged database credential enters the
frontend.

## Explicitly out of scope

Gate 1A introduces no Python or TypeScript implementation, SQL, migration,
repository, service, API, frontend or OpenAPI change. Phase 7-10 activation
itself will not call `run_once`, start `PaperTradingContinuousRunner`, create or
control processes/workers, publish runner or paper state, scan/synchronize RAW
data, call Binance, execute strategies, create orders/fills, settle PnL, or
implement pause/resume/stop.

Runtime epochs, process leases, heartbeat, desired state, supervisor behavior,
worker claims and fencing remain future runner-control work. Trading-horizon
labels, the ADT Official Portfolio, a second capital ledger, real-capital
execution, leverage, shorts and derivatives remain outside scope.

## Consequences / tradeoffs

The selected model adds historical rows and requires a uniqueness defense plus
fresh multi-authority checks at every activation and future start. It also
accepts that PostgreSQL cannot provide atomicity with local config files, so
effective eligibility is computed rather than stored as a permanent truth.

In return, the design preserves exact decision history, actor provenance and
idempotent retry behavior; avoids mutable-identity ABA; prevents an old grant
from overriding later revocation; keeps financial, administrative, filesystem
and runtime authorities separate; and provides a narrow, auditable input for a
future runner-control design without prematurely implementing it.
