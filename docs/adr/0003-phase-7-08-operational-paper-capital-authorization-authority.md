# ADR 0003 — Phase 7-08 Operational Paper Capital Authorization Authority

Status: Accepted

Date: 2026-08-27

## Context

Phase 7-07 introduced the durable `OperationalPaperSessionProfile`.

An approved profile freezes one exact approved operational mandate revision,
one mandate instrument, one canonical timeframe, one historical strategy
snapshot and deterministic non-capital paper-engine policies.

Capital was deliberately excluded from that aggregate. An approved profile is
therefore categorically non-runnable and cannot itself create a local
`PaperSessionConfig`, derive a deterministic `session_id`, publish paper files
or control a runner.

The existing Phase 5 local `PaperSessionConfig` includes initial simulated
capital as an identity-bearing input. Capital authority must therefore exist
before any operational profile can later be materialized into that local
configuration.

Phase 1 already provides the authoritative administrative simulated-capital
ledger through `simulation_runs` and append-only `capital_movements`.
Introducing a second capital ledger would create conflicting sources of truth.

## Decision

Phase 7-08 introduces a durable
`OperationalPaperCapitalAuthorization` aggregate.

It does not create a second ledger. The existing Phase 1 ledger remains the
authority for gross simulated capital. The new aggregate authorizes and
reserves a bounded amount of that capital for one exact approved
`OperationalPaperSessionProfile`.

The architecture becomes:

```text
simulation_runs + capital_movements
    -> authoritative gross simulated balance
    -> OperationalPaperCapitalAuthorization
    -> exact approved OperationalPaperSessionProfile
    -> future materialization authority/version
    -> existing immutable local PaperSessionConfig
    -> deterministic local session_id
```

Phase 7-08 stops at the capital-authorization boundary.

## Aggregate identity

`OperationalPaperCapitalAuthorization` has:

- stable PostgreSQL UUID `authorization_id`;
- schema version;
- lifecycle state;
- positive `record_version`;
- exact profile binding:
  - `profile_id`;
  - approved profile revision;
  - approved profile specification checksum;
- exact capital-source binding:
  - `simulation_id`;
  - `quote_asset`;
  - positive finite `authorized_capital`;
- canonical lowercase SHA-256 `authorization_checksum`;
- auditable creation actor/time;
- nullable revocation actor/time;
- actor-scoped create idempotency key;
- deterministic versioned create-intent fingerprint.

`authorization_id` never equals or predicts a future local `session_id`.

## Canonical authorization checksum

The authorization checksum covers canonical authorization semantics:

- authorization schema version;
- exact profile ID;
- exact approved profile revision;
- exact approved profile checksum;
- simulation ID;
- canonical uppercase quote asset;
- exact authorized capital.

It excludes:

- authorization UUID;
- lifecycle state;
- `record_version`;
- actors;
- timestamps;
- idempotency key.

The checksum represents what capital was authorized and for which exact
approved profile/source. It is not an audit-record checksum.

## Capital source and derived availability

The Phase 1 append-only ledger remains authoritative for gross balance.

No capital movement is inserted merely because capital is authorized.

The following values are derived:

```text
gross_balance =
    SUM(capital_movements.amount)

reserved_capital =
    SUM(authorized_capital)
    for AUTHORIZED capital authorizations

available_capital =
    gross_balance - reserved_capital
```

`reserved_capital` and `available_capital` are projections, not independent
persisted financial balances.

The public Phase 1 active-simulation projection is not redefined by 7-08.
Any new administrator projection must label gross, reserved and available
capital explicitly.

## Creation authority

Creating a new authorization requires:

1. an exact committed idempotency replay check before mutable-source
   revalidation;
2. an existing ACTIVE `simulation_run`;
3. the exact current approved profile revision/checksum;
4. profile state `APPROVED`;
5. exact equality between the simulation currency and the profile quote asset;
6. positive finite authorized capital;
7. sufficient currently available capital;
8. no other AUTHORIZED capital authorization for the same profile.

The browser is never authoritative for available-capital calculation,
profile state, checksum matching or reservation legality.

## Concurrency and reservation integrity

Reservation creation is serialized through the bound `simulation_run`.

The transaction must lock the relevant simulation row before calculating
ledger balance and active reservations.

Concurrent authorization requests against the same simulation therefore cannot
both consume the same available capital.

The invariant is:

```text
sum(AUTHORIZED authorized_capital for simulation)
    <= authoritative ledger current balance
```

Existing balance-decreasing ledger mutations must also preserve this invariant.
A capital movement may not reduce authoritative current balance below the
currently AUTHORIZED reserved total.

Database constraints/triggers remain defense in depth for financial
correctness; frontend calculations are never sufficient authority.

## Lifecycle

The lifecycle is:

```text
AUTHORIZED -> REVOKED
REVOKED    -> no transition
```

Authorized capital is immutable.

Changing the amount, profile binding or capital source requires explicit
revocation followed by a new authorization with a new administrator intent.

At most one AUTHORIZED authorization may exist per profile. Historical revoked
authorizations remain preserved.

Revocation requires the expected `record_version`.

## Profile archival

Later profile archival does not silently mutate or release an existing capital
authorization.

An AUTHORIZED authorization continues to reserve its amount until explicitly
revoked.

A later materializer must nevertheless fail closed if the bound profile is no
longer APPROVED.

This preserves explicit financial release and immutable historical evidence.

## Simulation terminalization

An ACTIVE `simulation_run` with one or more AUTHORIZED operational paper
capital authorizations may not become `COMPLETED` or `CANCELLED`.

The authorizations must first be explicitly revoked.

This prevents active reservations from remaining attached to a terminal capital
source.

## Idempotency

Create uses an actor-scoped idempotency key and a versioned deterministic
intent fingerprint.

The fingerprint covers stable administrator intent, including the exact profile
binding, simulation ID, quote asset and authorized capital. It does not include
mutable current balance.

Same actor/key/fingerprint is an exact committed replay.

Same actor/key with a different fingerprint conflicts.

A committed exact replay returns its original aggregate before mutable
simulation/profile state is re-evaluated.

A new authorization after revocation requires a new administrator intent and
idempotency key.

## Persistence boundary

PostgreSQL is authoritative for:

- authorization identity and lifecycle;
- exact profile/source binding;
- reservation state;
- concurrency tokens;
- administrator audit evidence.

The existing append-only capital ledger remains authoritative for gross
financial balance.

RLS must be enabled on new 7-08 tables. Browser/Data API roles receive no
authority. Administrative reads and writes occur only through the authenticated
FastAPI backend using direct PostgreSQL access.

## HTTP and frontend boundary

Future 7-08 HTTP remains bounded administrator transport.

It may:

- list capital sources and derived gross, reserved and available amounts;
- list authorizations;
- inspect one authorization;
- create one authorization;
- revoke one authorization.

It performs no strategy execution, Binance access, RAW scan, local filesystem
materialization, paper replay or long-running work.

The frontend sends administrator intent and concurrency tokens only.
It does not calculate authoritative balances, checksums or reservation legality.

## Explicitly out of scope

Phase 7-08 does not add:

- local `PaperSessionConfig` materialization;
- `session_id` derivation or reservation;
- `config.json` or `state.json` publication;
- paper replay or strategy execution;
- paper-runner or collector lifecycle control;
- trade PnL settlement into the administrative ledger;
- ADT Official Portfolio;
- official capital eras or resets;
- public portfolio history or projections;
- machine learning;
- Telegram;
- SaaS;
- exchange-account access;
- real-capital execution.

## Consequences

The next materialization delivery will have one exact approved profile and one
exact authoritative capital authorization available as inputs.

It must still define its own materialization authority and version and
revalidate all required current authorities before constructing the immutable
local `PaperSessionConfig`.

Phase 7-08 itself creates no runnable paper session.
