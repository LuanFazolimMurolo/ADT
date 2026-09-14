# ADR 0009 — Phase 7-15 Reviewed Trading-Horizon Labels

Date: 2026-09-14

## Context

Phase 7 has one remaining roadmap deliverable: reviewed trading-horizon
labels.

Earlier Phase 7 contracts deliberately deferred concepts such as `day trade`
and `swing trade` because they had no enforceable domain semantics. Phase 7-14
explicitly rejected adding a cosmetic horizon field.

The current authority split is:

- operational mandates authorize exact exchange/market/instrument scope;
- strategy definitions version reusable plugin and parameter configuration;
- operational paper-session profiles combine an approved mandate binding,
  selected instrument, timeframe, frozen strategy snapshot, execution
  assumptions, instrument constraints and risk policy;
- paper-session materialization converts an exact approved profile plus
  authoritative paper capital into an immutable local `PaperSessionConfig`;
- `PaperSessionConfig` determines `config_checksum` and `session_id`.

A candle timeframe alone cannot authoritatively determine whether the intended
trading horizon is day trade or swing trade.

The same strategy plugin may also be reused with different parameters,
timeframes or operational profiles. Therefore strategy identity alone is not
the correct trading-horizon authority.

## Decision summary

Phase 7-15 introduces a reviewed, explicit and identity-bearing
trading-horizon classification.

The initial supported labels are:

- `DAY_TRADE`
- `SWING_TRADE`

The label is administrator-selected as part of an operational
paper-session profile.

It is not inferred from timeframe, strategy name, strategy parameters,
historical holding duration, market regime or observed trade results.

The label becomes immutable when its profile revision is approved and is
included in canonical profile identity.

Materialization carries the reviewed label into the immutable local
`PaperSessionConfig`, where it participates in config checksum and session
identity.

## Horizon semantics

`DAY_TRADE` means that the approved operational profile is classified by the
administrator as intended for an intraday trading horizon.

`SWING_TRADE` means that the approved operational profile is classified by the
administrator as intended for a longer-than-intraday swing-trading horizon,
commonly spanning multiple days.

These labels describe reviewed operational intent.

They do not by themselves:

- force liquidation at a clock or calendar boundary;
- impose a maximum holding duration;
- create an exchange-session calendar;
- guarantee that realized trade duration matches the label;
- replace risk limits, stop-loss policy or strategy exit logic.

Any future executable holding-period policy requires a separate reviewed
contract.

## Authority placement

The authoritative label belongs to
`OperationalPaperSessionProfileSpecification`.

It does not belong to:

- `OperationalMandateSpecification`, because mandates authorize instruments and
  operational scope rather than one session's trading style;
- `StrategyDefinitionSpec`, because one reusable strategy may support multiple
  approved operational horizons;
- frontend-only state, because the horizon must be durable, auditable and
  checksum-bound.

## Profile contract versioning

Existing profile schema version `1` remains valid and represents a legacy
profile with no reviewed trading-horizon label.

Phase 7-15 introduces profile schema version `2`.

For schema version `2`:

- `trading_horizon` is mandatory;
- it must be one of `DAY_TRADE` or `SWING_TRADE`;
- it participates in the canonical specification payload;
- it participates in specification checksum/equality;
- it participates in create/replace request fingerprint semantics;
- approved revisions preserve it immutably.

Schema version `1` must not acquire a synthetic or inferred label.

No migration may rewrite existing v1 revision checksums.

## PostgreSQL persistence

A reviewed Phase 7-15 migration extends
`operational_paper_session_profile_revisions`.

The migration adds nullable `trading_horizon text`.

Database constraints enforce:

- schema version `1` => `trading_horizon is null`;
- schema version `2` => `trading_horizon in ('DAY_TRADE', 'SWING_TRADE')`.

Existing v1 rows therefore remain byte/semantic compatible and their historical
checksums remain unchanged.

The table remains backend-only with the existing RLS/Data API authority
boundary.

No new public database write authority is introduced.

## PaperSessionConfig identity

Legacy `PaperSessionConfig` schema versions `1` and `2` remain supported without
a trading-horizon label.

Phase 7-15 introduces `PaperSessionConfig` schema version `3`.

Schema version `3` requires one canonical trading-horizon label.

The label participates in the canonical config payload and therefore in:

- `paper_config_checksum`;
- `paper_session_id`.

This prevents two newly materialized sessions that differ only in reviewed
horizon from collapsing to the same local session identity.

Schema version `3` may carry the existing optional market-regime policy.

The horizon is identity-bearing metadata; the paper engine does not branch
trading behavior merely because the label differs.

## Materialization

Materialization from a profile v2 must copy the exact reviewed
`trading_horizon` into `PaperSessionConfig` schema version `3`.

Materialization from legacy profile v1 preserves the existing config-schema
selection and behavior.

The existing profile revision/checksum binding remains authoritative.

No browser-provided value may override the approved profile label during
materialization.

## Administrative API

Profile create/replace contracts expose `trading_horizon`.

New profile create operations use schema version `2` and require an explicit
supported label.

Replacing an existing DRAFT schema-v1 profile after Phase 7-15 requires an
explicit horizon and publishes a new schema-v2 revision. Existing APPROVED or
ARCHIVED schema-v1 revisions remain immutable and unlabeled.

Profile reads expose the persisted label.

Legacy v1 records return no reviewed horizon rather than receiving an inferred
value.

The administrator API must reject unsupported or missing horizon values for
new writes.

## Frontend

The administrator paper-session profile editor provides an explicit
trading-horizon selector with the two reviewed values.

The UI must not derive or auto-select a horizon from timeframe.

Profile list/detail/review surfaces expose the stored reviewed label.

Legacy unlabeled profiles are rendered as legacy/unclassified rather than being
silently assigned a label.

No public Official Portfolio surface is introduced by Phase 7-15.

## Compatibility and determinism

Phase 7-15 must preserve:

- all existing schema-v1 profile identities and checksums;
- all existing local `PaperSessionConfig` v1/v2 identities;
- existing materialized session IDs;
- strategy-definition identity;
- mandate identity;
- capital authorization identity;
- Phase 7-14 capital-era and settlement evidence;
- Decimal-only economic contracts;
- deterministic replay.

No migration may backfill an inferred horizon into historical profiles or
sessions.

## Explicitly out of scope

Phase 7-15 does not add:

- automatic timeframe-to-horizon classification;
- realized holding-duration classification;
- forced end-of-day liquidation;
- maximum holding-period enforcement;
- an exchange-session calendar;
- strategy recommendation or automatic strategy promotion;
- ADT Confidence Score;
- Official Portfolio aggregation;
- Telegram or subscriber distribution;
- billing;
- exchange credentials;
- live orders or fills;
- real-capital execution.

## Gate plan

### Gate 1 — contract

- accept this ADR;
- reconcile the Phase 7 roadmap/handoff;
- freeze the compatibility rules.

### Gate 2A — domain

- introduce `TradingHorizon`;
- version operational profile specification semantics;
- preserve legacy v1 compatibility;
- add deterministic domain tests.

### Gate 2B — persistence

- add the reviewed PostgreSQL migration;
- version repository hydration/insertion;
- prove legacy-row compatibility and RLS/Data API boundaries.

### Gate 2C — executable config identity

- add `PaperSessionConfig` schema v3;
- propagate the approved horizon through materialization;
- prove v1/v2 config/session identities remain unchanged.

### Gate 3 — administrator transport

- add bounded API request/response projection;
- update the generated OpenAPI TypeScript contract;
- add the administrator selector/read projection;
- reject inference and unsupported values.

### Gate 4 — integrated compatibility

- profile -> authorization -> materialization -> runner provenance;
- verify Phase 7-14 settlement remains compatible;
- verify legacy records remain readable and unchanged.

### Gate 5 — closure

- full backend/frontend/E2E validation;
- reviewed migration publication and local/remote parity;
- completion evidence;
- roadmap/handoff closure;
- pure fast-forward publication.

## Consequences

Trading-horizon labels become durable reviewed session identity rather than UI
decoration.

The project gains explicit `DAY_TRADE` and `SWING_TRADE` classification without
pretending that timeframe alone proves either category.

The delivery does not introduce new trading mechanics. A future enforceable
holding-period policy, if desired, must receive its own reviewed contract.
