# Phase 8-01 Gate 1 — ML dataset and feature contracts

## Boundary

`app/ml` is a pure immutable domain and document foundation. It performs no
feature calculation, snapshot reading, materialization, storage publication,
model fitting, prediction or recommendation. Construction and decoding invoke
no network client, database repository, operational service or strategy factory.

Schema v1 deliberately supports one exact STRICT derived snapshot and one
canonical timeframe per dataset. It reuses `TemporalSnapshotReference` and its
Phase 2C identity validation, `DataRange`, the timeframe registry,
`TemporalSegmentRole`, `IndicatorDescriptor`, typed strategy parameter documents
and the bounded Phase 4 canonical Decimal/SHA-256 primitives.

## Observation grain and source policy

An `MLObservation` is one decision point at the completion boundary of one
candle, optionally associated with an exact immutable strategy/config candidate.
Its identity binds its dataset specification, full snapshot/dataset identity
(including exchange, **market type**, symbol, timeframe, construction metadata
and versions), candle opening anchor, observation time and candidate. It does
not use `Candle.key` as a universal key and does not contain a label outcome.

`MLDatasetSpecification` binds `MLProvenance`, an ordered feature specification
and an optional target specification. A target makes accepted materialization
rows supervised: each must then contain its matching mature label. An
unsupervised specification accepts no label.

Provenance includes exact snapshot ID, dataset key/version/checksum, available
coverage, selected observation range and permitted feature context range. The
selected range lies inside context, which lies inside snapshot coverage. All
range boundaries are UTC, aligned and half-open. Snapshot references must pass
the existing STRICT derived-source validation and deterministic snapshot-ID
algorithm. Mutable RAW paths, `latest` dataset pointers and mutable PAPER state
are not source contracts.

No files are opened to authenticate these references. A future trusted source
adapter must verify the actual snapshot/manifest/partition evidence. In
particular, these references do not assert that available snapshot bytes were
actually observed by a historical live system.

## Event time and information time

A stored event timestamp is not proof of information availability. For example,
current backtest LIMIT/STOP fills can depend on candle high/low while carrying
an opening event timestamp. Such operational evidence is outside v1 sources.

For candle-derived information, v1 fixes:

```text
candle_available_at = candle.open_time + timeframe.duration
observation_time = anchor.open_time + timeframe.duration
feature_information_time <= observation_time
target_information_time > observation_time
```

For Binance and current resampled candles, stored close time is one millisecond
before this boundary. Neither that close timestamp nor `is_closed` alone can
override availability. `candle_available_at()` takes only the canonical opening
and timeframe; no caller-supplied close or availability override is accepted.

Example for a one-minute candle:

```text
anchor open                 10:00:00.000 UTC
stored source close         10:00:59.999 UTC
observation / availability  10:01:00.000 UTC
one-candle target maturity  10:02:00.000 UTC
```

`MLCandleDependency` describes complete contiguous source candles by their
half-open opening-time range. Its information time is derived as the range end:
the last included opening is `end - duration` and becomes available at `end`.
The range must be aligned and contained in its exact snapshot reference.

This is an event-time policy. Historical transport latency, publication delays,
revisions and actual arrival-time evidence are not reconstructed by this gate.

## Feature recipes and values

`MLFeatureSetSpecification` contains an ordered tuple of unique named
`MLFeatureSpecification` outputs and a required implementation reference.
Feature order is semantic: reordering outputs changes the feature-set identity.

Each output binds:

- Decimal, integer, boolean or categorical type;
- unit and semantic meaning;
- algorithm name/version/schema and canonical typed parameters, through the
  existing `IndicatorDescriptor`;
- ordered source fields from OHLCV, quote volume or trade count;
- history/lookback and warm-up candle counts;
- seed policy, reset policy and missing-value policy.

Only two history modes are supported. `TRAILING_WINDOW` uses at most the last
`lookback_candles`, resets `PER_OBSERVATION` and starts at the later of the
context start and dependency end minus lookback. `FROM_CONTEXT_START` resets
at `CONTEXT_START` and includes the entire prefix from that context start.
In this latter mode the declared lookback is recipe metadata; the dependency
range is the complete prefix, not a truncation to the nominal period. Warm-up
counts the initial unavailable source points. Actual algorithm seeding remains
the responsibility of the future recipe implementation.

`MLFeatureVector` binds exactly one observation and its exact feature set.
Values and dependencies have one entry per feature, in specification order.
Every dependency must reference the dataset's exact snapshot, fit inside the
declared context and satisfy the recipe history shape. A dependency may end
earlier than the observation, but never later. Its explicit range records this
historical prefix; the contract does not silently supply current values.

The vector derives its information time as the maximum of its dependencies.
These times are derived fields, not writable constructor inputs. Revalidation
and document decoding reject altered derived values.

Warm-up values must be `None`, never zero. After warm-up, missing values are
either rejected or explicitly preserved as `None` according to the recipe.
No imputing, backfilling or silent coercion is performed. Integers are distinct
from booleans; Decimal outputs do not accept floats, numeric strings or integers
as substitutes. Decimal values must be finite.

V1 accepts only `MLPreprocessing.NONE`. Fitted or learned preprocessing modes
are rejected. There is no scaler state, fitting range or learned transform.
Algorithm names/parameters are provenance declarations, not executable imports
or proof that an implementation respects its declaration.

## Targets, labels and maturity

`MLTargetSpecification` is separate from feature recipes. It binds a target
name, algorithm descriptor, type, unit/meaning, strictly positive integer future
horizon and required implementation reference.

V1 target coverage is fixed to the next contiguous `horizon_candles`:

```text
future range = [observation_time,
                observation_time + horizon_candles * timeframe.duration)
target_information_time = future range end
```

`MLLabel` binds exactly one observation, its exact target and a source dependency
covering precisely that future range. Its value is separate from feature
values. The horizon must fit the referenced snapshot. Changing the outcome
changes the label identity/checksum without changing observation identity.

A target algorithm may use an already-known anchor close as its baseline; the
observation and recipe identify that anchor. This does not relax the requirement
that the outcome matures strictly after observation time. No target calculation
is implemented here, and there is no label `None` placeholder in an admitted
supervised row.

## Temporal membership and training cutoff

`MLTemporalSplit` reuses the existing TRAIN, VALIDATION and TEST role enum and
opening-anchor membership semantics. Its three aligned half-open ranges are
contiguous, non-overlapping and consume the dataset's selected range exactly.
An observation at the closing boundary remains associated with its candle's
opening anchor, rather than being reassigned to the next role.

Each admitted label's entire future range must fit its observation's scored
role. In addition, TRAIN requires:

```text
target_information_time <= training_cutoff <= train.end
```

The cutoff cannot precede `train.start`. Equality at maturity is accepted;
even a one-microsecond shortfall is rejected. The terminal observations of a
role whose labels mature across its end cannot be admitted, even if their
future data exists in the snapshot.

`allow_retrospective_context` separately controls whether feature dependencies
may reach into earlier historical coverage. If false, all feature dependencies
must start inside the assigned role. This permission never permits a target to
reach a later role.

`REJECT_CROSSING` is the only v1 boundary policy. There is no purge/embargo
algorithm and no automatic row selection. A future caller may explicitly
exclude a row with one of the supported, checked reasons: target crosses its
role boundary, target exceeds training cutoff, target exceeds source coverage,
or feature warm-up. The reason must be consistent with the observation and
split. Multiple reasons can apply; choosing a reason is explicit manifest data.

One split may represent one existing three-way window/fold; it does not alter
Phase 4 planning, fold progression or selection. Cross-fold model fitting,
global holdout discipline and aggregate evaluation remain future work.

## Provenance and implementation identity

`MLProvenance` can additionally bind algorithm descriptors and one exact
strategy reference. Required feature/target algorithm identities also reside in
the dataset specification. Optional provenance algorithms describe additional
source/build steps and their order is identity-bearing.

`MLStrategyReference` contains plugin name/version, plugin schema/lifecycle,
the existing typed immutable parameter document and its checked parameter
checksum, full configuration checksum and implementation reference. A mutable
strategy-definition UUID alone is never accepted. A candidate may be attached
to an observation; when dataset provenance binds one strategy, every observation
must carry that exact reference. Distinct explicit candidates at the same time
are distinct observations.

Configuration checksums are immutable references, not a reconstructed executable
configuration. A future trusted producer must retain and authenticate the full
referenced configuration, including fees, capital, risk, execution/terminal
behavior and context assumptions. Parameter checksums alone do not identify a
plugin or complete evaluation configuration.

`MLImplementationReference` requires name, version and content SHA-256 supplied
by the future trusted producer. It must identify actual recipe/build semantics,
including relevant dependency and arithmetic behavior. Feature sets, targets,
provenance, strategy references and materializations bind such a reference.
HEAD alone may be insufficient when the source tree is dirty. The ML package
does not inspect Git, run subprocesses or hash the repository automatically.

## Immutable materialization manifests

`MLDatasetMaterialization` is only an in-memory manifest. It contains a dataset,
split, implementation reference, accepted rows and explicit exclusions.
`MLMaterializationRow` binds a vector, optional label and role, plus derived
observation ID, vector checksum and label checksum. The manifest derives its
accepted-observation, label and exclusion counts.

Membership is sorted by candle opening then observation identity; exclusions
use the same ordering. Constructor input order therefore does not change the
logical identity. Duplicate observation identities are rejected across accepted
rows and exclusions, including duplicate rows with different label outcomes.
The manifest enumerates exactly the supplied membership; it does not claim
that every possible candle/candidate in coverage has been enumerated.

`built_at` is optional UTC audit metadata. It changes canonical document bytes
and the outer document checksum, but not logical checksum or identity. It is
not substituted for feature availability or target maturity.

## Canonical documents and errors

All ML value objects have schema version 1. Logical checksum is SHA-256 of their
canonical logical payload; identities use separate
`adt-ml-<contract-type>-v1` SHA-256 namespaces. Changing a contract type is a wire
identity change. No clock, randomness or process-local hash enters identity.

`to_document` / `from_document` operate on fresh JSON-compatible mappings;
`encode_document` / `decode_document` operate on canonical UTF-8 bytes. Every
envelope includes explicit schema, payload, logical checksum, identity and an
outer document checksum. The codec uses a closed registry of explicit types,
never an import path, pickle or arbitrary object hook.

Values use explicit tags for Decimal, UTC datetime, enum and value-object types.
Decimal text is exact, context-independent and canonical (`-0` becomes `0`);
Decimal is never confused with a string or floating-point value. Datetimes use
canonical `isoformat()` UTC with `+00:00`. Object keys are sorted and JSON is
compact ASCII-escaped UTF-8. Feature order is preserved. Mapping key order is
irrelevant; the byte decoder rejects noncanonical whitespace/encodings.

Duplicate JSON keys, unknown/missing fields, floats, NaN/infinity, unsupported
schemas, invalid checksums, unknown type tags and incompatible references are
rejected. Recomputed hashes cannot legalize invalid temporal semantics, false
counts, derived timestamps, role assignments or duplicate membership.

Limits: 128 features/parameters, 1,000 total accepted/excluded observations,
10,000,000 candles per bounded range/count, 256-character domain text,
512-character maximum document strings, 128-digit integers, 128-character
canonical Decimal text, 40 JSON nesting levels, 300,000 JSON nodes and 16 MiB
per complete document. All limits apply; the byte/node limits can admit fewer
rows for larger recipes because the self-contained document repeats references.

Stable ML-domain errors distinguish invalid input, temporal leakage,
incompatible reference, duplicate observation, unsupported schema and corrupt
document. Encoding/consumption revalidates frozen objects to detect low-level
mutation; decoded derived metadata must equal its independently derived value.

## Structural validity is not causal verification

A checksum proves a content relationship, not truth. This package verifies
declared source identities, dependency ranges, time inequalities, types and
cross-references. It cannot prove that a supplied value was calculated from the
declared dependency rather than copied from a future label. There is no
self-authorizing `verified=True` flag.

A later trusted materializer must authenticate source bytes, reproduce the
declared calculation on bounded prefixes, verify recipe versions and actual
dependency completeness, and establish training/serving parity. Prefix-oriented
tests in this gate exercise structural cutoffs; they do not claim calculator
causality or model quality. Earlier indicator prefix-equivalence contracts
remain reusable evidence for that later gate.

## No execution authority and explicit exclusions

No contract grants mandate approval, capital authorization, activation or
operational START/PAUSE/RESUME/STOP authority. The package imports no PAPER
service, operational worker/command service, capital/activation service,
network client or database repository. Its own imports are tested against an
explicit list of reused domain/canonical modules, and construction/codec tests
block file, socket and subprocess use.

This gate adds no ML dependencies, training, fitted preprocessing, predictions,
recommendations, confidence score, feature calculator, snapshot reader,
materializer, repository, database migration, API, worker, frontend or
PAPER/LIVE integration. It does not change roadmap/handoff/agent instructions or
the existing deterministic execution and authorization boundaries.
