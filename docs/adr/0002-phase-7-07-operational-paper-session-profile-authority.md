# ADR 0002: Phase 7-07 operational paper-session profile authority

- **Status**: Accepted — Gate 1
- **Date**: 2026-08-23
- **Scope**: Phase 7-07

## Context

Phase 5 already has an executable local `PaperSessionConfig`. It is an
immutable, content-addressed replay contract stored below `ADT_DATA_DIR`; its
canonical bytes include initial simulated capital, and its deterministic
`session_id` identifies the complete executable configuration. The local paper
runner consumes explicit `session_id` values and performs real replay work
outside FastAPI.

Phase 7-06 separately established an immutable administrator-approved mandate
authority. A consumer can bind to an exact
`mandate_id + approved_revision + specification_checksum`, but a mandate does
not select a timeframe, strategy or deterministic paper-execution policy.

Phase 7-07 needs a durable administrator workflow between these boundaries. It
must not pretend that a PostgreSQL UUID is a local paper `session_id`, invent
capital authority, execute a strategy, or duplicate mandate and strategy
authorities.

Repository evidence also establishes that:

- Phase 3C strategy definitions are revisioned active rows, but replacement
  overwrites the current payload and does not preserve each payload in a
  separate immutable revision row;
- supported timeframes come from the immutable in-process `TIMEFRAMES`
  registry;
- fees, fixed-basis-point slippage, position sizing, stop loss, risk limits,
  engine bounds and optional market-regime policy already have deterministic
  backend value contracts;
- `InstrumentConstraints` are currently explicit local CLI inputs, with local
  defaults, rather than durable Binance metadata or mandate fields; and
- the live asset catalog is an expiring in-memory network projection and does
  not provide a durable execution-constraint authority.

## Decision

### Terminology and authority

The new aggregate is named **`OperationalPaperSessionProfile`**. The public
product term is **operational paper-session profile**.

A profile is a durable, authenticated, auditable administrator-approved
specification for deterministic paper operation. It is never itself a running
session. The existing executable `PaperSessionConfig` name and semantics remain
unchanged.

PostgreSQL will be authoritative for profile intent, immutable specification
history, approval, archival, administrator attribution, idempotency and
optimistic concurrency. It will not be authoritative for local paper artifacts,
runner state, capital balances, positions, orders, fills or replay results.

### Identity model

The model has distinct identities:

1. `profile_id`: stable PostgreSQL UUID for the aggregate;
2. `revision`: positive integer identifying one immutable profile
   specification revision;
3. `specification_checksum`: lowercase SHA-256 of canonical specification
   semantics;
4. `record_version`: positive aggregate concurrency token;
5. a future materialization identity binding an approved profile to later
   capital and materialization authority; and
6. the existing deterministic local `session_id`, derived only from a complete
   local `PaperSessionConfig`.

The UUID never equals, aliases or predicts the content-addressed local
`session_id`. Revision metadata, actors, timestamps, aggregate state and
`record_version` are excluded from the specification checksum.

### Specification boundary

One immutable profile specification freezes:

- schema version, bounded name and bounded description;
- exact mandate binding: `mandate_id`, `approved_revision` and mandate
  `specification_checksum`;
- exactly one canonical Binance Spot instrument selected from that bound
  mandate revision;
- exactly one canonical timeframe code from `TIMEFRAMES`;
- aligned UTC evaluation `start_at` and a bounded nonnegative warmup count;
- one frozen strategy snapshot;
- deterministic execution assumptions and position-sizing policy;
- explicit deterministic instrument constraints;
- deterministic risk limits and optional stop-loss policy;
- bounded history, candle, order and event limits plus engine version; and
- an optional versioned market-regime policy.

The specification freezes resolved values. It does not reference mutable
environment defaults. The future local paper document schema is derived by the
future materialization contract from these frozen semantics; it is not a
second independently mutable profile field.

### Create intent and server resolution

An administrator create intent is distinct from the server-resolved immutable
profile specification. The intent carries `strategy_definition_id`,
`expected_strategy_definition_revision`,
`expected_strategy_parameters_checksum`, the exact mandate binding, selected
instrument, timeframe and every other explicit administrator-authored policy,
constraint and profile input. The expected strategy revision is the required
full-row review and concurrency token. The parameters checksum is a useful
additional review token exposed by the existing Phase 3C read contract, but it
cannot replace revision because plugin, schema or lifecycle identity may change
without a parameter change.

The administrator and browser are not authority for the resolved strategy
snapshot. For a genuinely new create intent, the backend resolves the current
strategy-definition row, requires it to be `ACTIVE` at the exact expected
revision and parameters checksum, resolves its exact plugin through the
explicit server-side registry, constructs the complete frozen strategy
snapshot, validates the mandate and remaining profile semantics, and only then
persists the new aggregate and its server-resolved specification atomically.

### Exact mandate binding

Create, changed draft replacement and approval require an exact existing
`APPROVED` mandate at the supplied revision and checksum. The selected
instrument must be a member of that immutable revision.

The profile persists only its selected instrument and the exact mandate
binding. It does not copy the mandate's complete instrument set or become an
independent instrument-authorization source.

Approval performs no live-availability check. A later mandate archive does not
rewrite profile history or automatically change profile state. It does make
future materialization fail closed because the mandate remains the operational
authorization authority.

### Frozen strategy snapshot

The profile snapshot retains:

- `strategy_definition_id`;
- source strategy-definition revision as historical snapshot evidence;
- plugin name and version;
- plugin schema version;
- strategy lifecycle version;
- canonical normalized parameter document;
- source `parameters_checksum`; and
- a profile-local strategy-snapshot checksum over all snapshot semantics.

Create and changed draft replacement resolve an `ACTIVE` definition through
the existing service and registered server-side plugin registry. Approval
revalidates that the source definition is still `ACTIVE`, remains at the exact
snapshotted revision and payload, and resolves to the registered plugin under
the atomic transaction contract below. If it changed after the draft was
recorded, approval conflicts and the draft must be replaced with a new
immutable profile revision before review.

Phase 3C does not retain an immutable row for each strategy revision. The
stable `strategy_definition_id` may reference `strategy_definitions.id`, whose
deletion is forbidden, but `(strategy_definition_id, source_revision)` must
not be modeled as a foreign key to a nonexistent immutable revision object.
The persisted source revision, plugin/schema/lifecycle identity, canonical
parameters, `parameters_checksum` and profile-local strategy-snapshot checksum
are the profile's immutable historical evidence. Later source-row revision
increments remain legal and must not violate older draft or approved profile
rows merely because their snapshots record an earlier revision.

After approval, later source-definition replacement or archival does not mutate
or invalidate the frozen approved profile. The approved profile snapshot is the
historical strategy evidence. A future materializer uses that snapshot and
must still resolve the exact plugin identity from the explicit server registry;
arbitrary or dynamically imported code remains forbidden. Revocation of future
use is expressed by archiving the profile or its bound mandate, not by silently
rewriting profile history.

### Timeframe and trading horizon

The initial profile selects exactly one supported canonical timeframe. No new
timeframe table or mutable authority is introduced.

Trading-horizon concepts such as day trade or swing trade have no current
enforceable domain semantics and remain out of scope. No cosmetic horizon label
is stored, and the separate roadmap deliverable remains open.

### Deterministic policy snapshot

The profile freezes every current non-capital, identity-bearing input needed by
the local paper configuration as distinct nested canonical value contracts:

- execution assumptions: fee model, slippage model, conservative intrabar
  policy and paper-required `force_close_at_end=false`;
- position-sizing policy, including its own sizing value and quote-reserve
  semantics;
- instrument constraints: quantity, step, tick and notional boundaries;
- risk limits, including their independent order, position, drawdown,
  all-in and quote-reserve semantics;
- optional stop-loss policy nested according to the existing risk contract;
- history, replay-candle, order and event bounds and engine version;
- evaluation start and lifecycle-compatible warmup; and
- optional market-regime policy with its existing versioned structure.

Fields with similar names in different value objects, including quote-reserve
semantics, remain independently represented. Gate 1 does not flatten these
contracts or invent a replacement financial schema.

The contracts reuse current backend value semantics. Approval only validates
and freezes them; it performs no sizing, risk evaluation, indicator calculation
or strategy execution. Equity-dependent sizing remains non-runnable until
capital is bound. Reusing these policies does not close the broader roadmap
item for all future strategy, sizing, fee and risk-policy administration.

### Instrument execution constraints

The initial authoritative source is explicit administrator deterministic input
inside the profile specification, matching the current local paper-session CLI
boundary. The frozen fields are minimum quantity, quantity step, price tick,
minimum notional and optional maximum notional.

These values are paper-simulation execution assumptions, not claims about
current Binance metadata. They are not added to the mandate, are not obtained
from the expiring asset catalog, and require no network call at approval. All
values must be explicit in the authoritative request and canonically validated
by the backend; frontend defaults are not authority. A later delivery may add a
separately reviewed durable exchange-metadata source, but it cannot rewrite an
approved profile.

### Capital and materialization

Capital is absent from the 7-07 specification. The profile does not bind Phase
1 simulation capital, arbitrary local initial capital, future operational
allocation or ADT Official Portfolio capital.

The architectural equation is:

```text
approved OperationalPaperSessionProfile
    + future authoritative capital binding
    + future materialization authority/version
    = existing immutable local PaperSessionConfig
    = deterministic local session_id
```

Consequently, neither a draft nor an approved profile is runnable. No 7-07
operation may create `config.json`, derive or reserve a local `session_id`,
allocate capital or write below paper-session storage.

### Lifecycle

The lifecycle is:

```text
DRAFT -> APPROVED
DRAFT -> ARCHIVED
APPROVED -> ARCHIVED
ARCHIVED -> no transition
```

This lifecycle is justified by profile semantics rather than copied as runtime
state: draft permits specification review, approval seals one exact revision,
and archival retires future use. `APPROVED -> DRAFT` is forbidden. No revision
may be appended after approval. `ARCHIVED` is terminal. No running, paused,
stopped, failed or materialized state belongs to this aggregate.

Archive records the authenticated actor and authoritative timestamp. A draft
may be archived without approval, with nullable approval metadata preserving
that distinction. Profile archival prohibits future materialization but does
not delete or mutate historical specification revisions.

### Revision, concurrency and idempotency

Create accepts a complete administrator intent, authenticated actor UUID and
explicit non-sensitive idempotency key. Its actor-scoped identity is:

```text
create_intent_fingerprint =
    SHA256(versioned canonical administrator intent)
```

The canonical intent includes the strategy-definition ID, expected source
revision, expected parameters checksum, exact mandate binding and every
explicit profile input. It is computable from the original request semantics
and does not re-resolve a possibly changed strategy row merely to recognize an
already committed replay.

Create processing first validates and canonicalizes request shape sufficiently
to compute that stable fingerprint, then checks `actor_id + idempotency_key`.
An existing key with the same fingerprint returns the originally created
aggregate; an existing key with a different fingerprint conflicts. Only a new
key and intent resolve and validate the mutable strategy source and registered
plugin, construct the resolved specification, validate mandate/profile
semantics and persist atomically. Therefore, an exact committed replay remains
an exact replay after the source strategy is edited or archived. This replay
rule does not bypass the separate source-freshness requirements for later
approval. No separate idempotency table is required by Gate 1; Gate 2B may
select a schema only from implementation and persistence evidence.

Creation starts at revision 1 and `record_version` 1. A draft replacement
requires `expected_revision` and `expected_record_version` before semantic
comparison. A semantically identical replacement is `NOOP`: it appends no
revision and increments neither token. Stale tokens conflict even when the
submitted specification would otherwise be identical. A changed replacement
appends one immutable revision and increments `record_version` once.

Approval requires `expected_revision`, expected profile specification checksum
and `expected_record_version`. A successful approval runs in one PostgreSQL
transaction and seals that exact revision while incrementing `record_version`.
Within the same commit boundary, row locks or equivalent atomic SQL predicates
must establish all of the following:

- the profile is `DRAFT` and its expected revision, specification checksum and
  `record_version` all match;
- the exact bound mandate ID, immutable approved revision and specification
  checksum match, and the current mandate aggregate state is `APPROVED`;
- the strategy source ID matches, its current state is `ACTIVE`, its revision
  equals the frozen source revision, and its plugin name/version,
  plugin-schema version, lifecycle version, canonical parameters and
  `parameters_checksum` all match the snapshot; and
- the exact snapshot plugin identity remains resolvable from the explicit
  in-process server registry, without network access.

The database transaction must prevent mandate archival, strategy
replacement/archival and competing profile lifecycle mutation from racing
between validation and approval. If a source mutation obtains authoritative
state first, approval conflicts or fails. If approval commits first, a later
source mutation follows the accepted historical and future-materialization
rules: mandate archival makes materialization fail closed, while strategy
replacement or archival does not rewrite the frozen approved snapshot.

The transaction remains bounded and is never held open during network or
Binance I/O, filesystem work, RAW reads, replay, plugin execution or other
long-running work. Archive requires `expected_record_version` and increments
it once without creating a new specification revision. Revision history is
bounded and ordered deterministically.

### Non-runnable HTTP and runtime boundary

Future 7-07 HTTP may expose bounded administrator-only list, point-read,
history, create, draft-replace, approve and archive operations. FastAPI remains
transport only. A later Gate 4 protected frontend may consume only this API and
never becomes checksum, financial-policy or lifecycle authority.

No 7-07 HTTP, service or repository path may:

- execute a strategy or paper replay;
- invoke Binance or require live instrument availability;
- scan RAW data;
- create or mutate local paper files;
- derive a local `session_id`;
- start, pause, resume or stop a runner, collector or worker;
- allocate or mutate capital; or
- perform long-running work.

Runtime impact is exactly none.

### Future materialization evidence

A later reviewed delivery must consume at least:

- `profile_id`, approved revision and profile specification checksum;
- exact mandate ID, approved revision and checksum;
- selected canonical instrument and timeframe;
- frozen strategy snapshot identity and checksum;
- complete deterministic policy, bounds and constraint snapshot;
- authoritative capital-binding identity and amount semantics; and
- an explicit materialization-contract version.

It must revalidate that the profile remains `APPROVED`, the profile's bound
mandate remains `APPROVED`, the frozen plugin identity is registered, and all
evidence agrees before constructing the existing canonical
`PaperSessionConfig`. It consumes the approved frozen strategy snapshot and
must never silently substitute the latest strategy-definition row payload.
Historical validity does not require that mutable Phase 3C row to remain at the
old source revision. Materialization, file publication, runner subscription
and activation remain outside 7-07.

### Persistence and deployment direction

Gate 2B is expected to add a migration for a stable profile aggregate,
immutable specification revisions and canonical snapshot substructures. The
database must enforce lifecycle, history, identity, actor, idempotency and
concurrency defenses. RLS is enabled and all Data API privileges are revoked;
only the backend direct PostgreSQL connection may access the authority.

The exact 7-06 mandate revision/checksum binding may use relational enforcement
against the existing immutable mandate specification revisions. Strategy
persistence is intentionally different: `strategy_definition_id` may reference
the durable Phase 3C identity, while source revision and the complete strategy
snapshot are immutable profile fields rather than a composite foreign key to a
historical strategy revision. Gate 2C/2E must implement approval's database
checks in the single transaction described above.

No migration is created by Gate 1. Existing migrations, including the 7-06
mandate migration, remain versioned and remotely unapplied. Disposable local
PostgreSQL may apply the versioned chain for implementation tests. Linked or
remote application remains a separate reviewed operational action.

## Alternatives considered

### Reuse `PaperSessionConfig` as the PostgreSQL aggregate

Rejected. That type requires capital and produces the executable local
`session_id`, conflating administrative approval with materialization and
runtime identity.

### Use the profile UUID as local `session_id`

Rejected. It would discard the existing content-addressed deterministic
identity and make local execution depend on mutable database identity.

### Store only a strategy-definition reference

Rejected. The Phase 3C row is revised in place, so an old approved profile
would lose its exact historical payload.

### Copy strategy code into the profile

Rejected. Only explicit registered server-side plugin identities are allowed.

### Fetch Binance constraints during approval

Rejected. Approval must be deterministic, bounded and network-free; the
current catalog is expiring and does not expose a durable constraint contract.

### Add execution constraints to the mandate

Rejected. A mandate authorizes canonical instruments and deliberately excludes
mutable adapter/execution metadata.

### Include capital in 7-07

Rejected. The repository contains multiple distinct capital concepts and no
approved operational allocation authority. Including it would require a scope
amendment and risk pre-empting Official Portfolio and capital-era design.

## Consequences

Positive consequences:

- mandates remain the sole instrument-authorization authority;
- approved profile behavior remains reproducible after strategy-definition
  edits;
- future capital and runtime work receives immutable, auditable inputs;
- approval is network-free and performs no physical work; and
- existing local paper identities and CLI compatibility remain unchanged.

Costs and limitations:

- an approved profile is intentionally not yet executable;
- administrators must supply explicit simulation constraints;
- a later capital/materialization delivery is mandatory before runtime use;
- profile and mandate archival must be checked by that future materializer; and
- trading-horizon and broader policy-administration roadmap items remain open.
