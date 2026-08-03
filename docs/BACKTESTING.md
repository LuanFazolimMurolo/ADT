# Deterministic Backtesting (Phases 3A–3B and Phase 4 input contracts)

Phase 3A implements a local, reproducible, candle-by-candle backtest engine for
one immutable Phase 2C snapshot. It is a technical simulation facility, not a
financial recommendation and not an order-routing system.

## Scope

The implemented boundary is intentionally narrow:

- one snapshot, instrument and timeframe per run;
- crypto Spot only;
- long-only, no leverage, margin, shorts or derivatives;
- quote-asset initial capital and base-asset position;
- `Decimal` for all financial calculations;
- no external network access during planning, execution or verification;
- no arbitrary Python module loading from the CLI.

`NoOpStrategy` and `BuyAndHoldExample` are technical examples. The latter is
not a trading recommendation. `ScriptedStrategy` exists only for deterministic
tests and is not registered in the operational CLI.

## Immutable input

The engine consumes only a Phase 2C snapshot. At open time it validates the
snapshot metadata, copied dataset manifest and partition checksums. After the
last candle it calls `verify_unchanged()` again. A changed snapshot aborts the
run and no `COMPLETE` result is published.

Intervals are half-open: `[start, end)`. When the CLI omits `--start` and
`--end`, the complete snapshot range is used.

## Candle cycle and future-leakage prevention

For every closed candle `T`, the engine performs this order:

1. evaluate orders that became eligible before `T`;
2. generate deterministic fills from the current OHLC values;
3. apply fills to portfolio and ledger;
4. mark the portfolio using the close of `T`;
5. append `T` to the bounded strategy history;
6. call `on_candle` with only processed history;
7. validate returned intents and create orders eligible from `T+1`.

An order created by `on_candle` at `T` cannot fill at `T`. A market order fills
at the open of its first eligible candle. Strategies never receive the reader,
filesystem paths, future iterators or mutable engine state.

## Orders and fills

Phase 3A supports:

- sides: `BUY`, `SELL`;
- types: `MARKET`, `LIMIT`, `STOP_MARKET`;
- time in force: `GTC`, `IOC`, `DAY`;
- all-or-none fills only.

Priority is deterministic: eligible candle, creation sequence, then order ID.
OHLC data cannot reveal the real intrabar path. The manifest therefore records
a conservative intrabar assumption and the engine never invents tick data.

Market and stop-market executions use adverse fixed-basis-point slippage and
taker fees. Limit fills never violate the submitted limit and use the configured
maker fee assumption. Fees are charged in the quote asset.

## Portfolio, risk and ledger

The Spot portfolio tracks quote cash, base quantity, average entry price, cost
basis, realized and unrealized PnL, fees, slippage cost, equity, peak equity and
drawdown. Cash and position can never become negative. Sales use average-cost
accounting and may close only available quantity.

Risk validation covers instrument precision, minimum quantity/notional, order
and position notionals, open/total order limits, quote reserve and maximum
drawdown. A configured drawdown halt cancels open orders, blocks new orders and
continues mark-to-market until the interval ends.

The local backtest ledger is append-only and separate from the Supabase paper
simulation ledger. Every entry contains a sequence and chained SHA-256 hash.
Verification detects modification, deletion, reordering, duplication or broken
balances.

## Metrics

Phase 3A derives deterministic return, PnL, fee, slippage, drawdown, order, fill,
closed-trade, win-rate, profit-factor, expectancy, exposure, turnover and
buy-and-hold comparison metrics. Undefined divisions are represented as `null`,
not infinity.

Phase 3B adds time-normalized metrics from the immutable equity curve:

- `return_periods` and exact `elapsed_seconds`;
- observed `periods_per_year`, derived from actual UTC timestamps;
- CAGR using a fixed 365-day crypto year;
- annualized population volatility and downside deviation;
- Sharpe and Sortino ratios using a zero risk-free rate.

The first production return period starts at the configured half-open backtest
range start and ends at the first closed candle. Later periods are measured
between consecutive candle close timestamps. Observation timestamps must be
strictly increasing. A zero denominator is represented as `null`, never
infinity. Decimal calculations use an explicit high-precision local context.

Schema version 1 retains the exact Phase 3A metric payload and checksum.
Schema version 2 includes the advanced Phase 3B fields. This allows the current
verifier to validate previously published Phase 3A artifacts without rewriting
or mutating them.

The second Phase 3B delivery adds a versioned comparison report contract. It:

- accepts between 2 and 100 unique run IDs;
- verifies every immutable run before reading its summary;
- projects only bounded, visualization-safe identity and metric fields;
- supports deterministic ordering by return, CAGR, Sharpe, Sortino, drawdown,
  net profit or profit factor;
- keeps undefined advanced metrics last and uses run ID as a stable tie-breaker;
- exposes whether all entries share the same snapshot, data range and initial
  capital, instead of silently implying an apples-to-apples comparison;
- reads schema version 1 runs with unavailable advanced metrics represented as
  `null`.

The local command is:

```bash
python -m app.cli backtest compare \
  --run-id <RUN_A> \
  --run-id <RUN_B> \
  --sort-by sharpe_ratio
```

Use `--ascending` when lower values should appear first. The command performs no
network request and does not create or mutate artifacts.

The third Phase 3B delivery publishes a portable, content-addressed comparison
export only after every source run passes full verification:

```bash
python -m app.cli backtest compare-export \
  --run-id <RUN_A> \
  --run-id <RUN_B> \
  --sort-by sharpe_ratio \
  --yes
```

Exports are written atomically under
`ADT_DATA_DIR/market/backtest-reports/<report_id>/` and contain exactly
`manifest.json`, `report.json` and `report.csv`. The report ID is derived from
the complete canonical comparison report, so repeated exports are idempotent.
The manifest binds ordered run IDs, logical result checksums and file checksums.
The CSV uses a fixed column order and represents undefined metrics as empty
cells. Verify an export independently with:

```bash
python -m app.cli backtest compare-verify --report-id <REPORT_ID>
```

The fourth Phase 3B delivery adds two read-only, network-free contracts.

A verified equity curve can be projected to at most 2,000 uniformly sampled
points for a chart or administrative preview:

```bash
python -m app.cli backtest visualize \
  --run-id <RUN_ID> \
  --max-points 500
```

The contract preserves the first and last observations, returns only timestamp,
close, equity and drawdown percentage, and includes the verified logical-result
checksum. The complete run is verified before Parquet is read. The source
artifact remains unchanged and no chart file is written.

Multiple explicit comparisons can be evaluated in one bounded request without
creating a parameter grid or selecting a strategy automatically. The JSON file
must contain between 1 and 20 named groups, at most 500 run references and at
most 100 unique runs:

```json
{
  "contract_version": 1,
  "groups": [
    {
      "name": "baseline",
      "run_ids": ["<RUN_A>", "<RUN_B>"],
      "sort_by": "sharpe_ratio",
      "descending": true
    }
  ]
}
```

Run it with:

```bash
python -m app.cli backtest compare-batch --request-file ./comparison-batch.json
```

Every unique run is verified exactly once and reused across groups. The response
has a deterministic `batch_id` bound to all ordered comparison reports. Batch
comparison is read-only: it does not execute backtests, search parameter spaces,
rank a strategy for deployment or publish artifacts.

## Result artifacts

A complete run is published atomically under:

```text
ADT_DATA_DIR/
  market/
    backtests/
      <run_id>/
        manifest.json
        config.json
        result.json
        orders.jsonl
        fills.jsonl
        ledger.jsonl
        equity.parquet
        trades.jsonl
```

The deterministic `run_id` includes the snapshot identity, strategy descriptor,
canonical parameters, capital, interval, execution assumptions, risk limits,
engine version and schema version. Operational timestamps are excluded from the
logical identity.

Publication writes a staging directory, fsyncs artifacts, writes the manifest
last and atomically renames the directory. Existing valid results are reused;
corrupt or logically conflicting results are rejected and never overwritten.

## CLI

Planning performs no writes:

```bash
.venv/bin/python -m app.cli backtest plan \
  --snapshot-id <snapshot-id> \
  --strategy no-op \
  --initial-capital 10000
```

The example strategy requires an explicit base quantity:

```bash
.venv/bin/python -m app.cli backtest run \
  --snapshot-id <snapshot-id> \
  --strategy buy-and-hold-example \
  --quantity 0.01 \
  --initial-capital 10000 \
  --yes
```

A dry run executes the engine but writes no result directory:

```bash
.venv/bin/python -m app.cli backtest run \
  --snapshot-id <snapshot-id> \
  --strategy no-op \
  --initial-capital 10000 \
  --dry-run
```

Inspect and independently verify a result:

```bash
.venv/bin/python -m app.cli backtest inspect --run-id <run-id>
.venv/bin/python -m app.cli backtest verify --run-id <run-id>
.venv/bin/python -m app.cli backtest orders --run-id <run-id> --limit 20
.venv/bin/python -m app.cli backtest trades --run-id <run-id> --limit 20
```

The CLI uses a fixed strategy registry and never imports a module supplied by a
user. Output is bounded JSON. Backtest commands are routed before market HTTP
clients are constructed.

## Phase 4-02 temporal input contracts

Temporal segmentation is a pure planning-input contract and does not call the
backtest engine. `TemporalSegmentationService` validates an immutable Phase 2C
snapshot against its manifest, requires a `STRICT` derived dataset and divides
an explicit selected coverage into exactly three evaluation ranges:

```text
TRAIN [start, validation_start)
VALIDATION [validation_start, test_start)
TEST [test_start, selected_end)
```

The version 1 `CONTIGUOUS_THREE_WAY` policy accepts only positive integer
`train_candles`, `validation_candles` and `test_candles`. Their sum must equal
the exact timeframe-slot count of the selected range. All UTC boundaries use
the existing timeframe registry and `[start, end)` semantics; therefore a
boundary candle belongs only to the segment that starts there. There are no
gaps, overlaps, purge windows, embargoes, percentages or implicit rounding.

A single non-negative `warmup_candles` value applies to every segment. The
future reader interval is `[context_start, evaluation_end)`, but only
`[evaluation_start, evaluation_end)` is scored. Validation context may include
earlier TRAIN candles and TEST context may include earlier VALIDATION candles;
they remain context only. Insufficient prior snapshot history is rejected
instead of truncating warmup, synthesizing candles or reading future data.

Schema version 1 binds the snapshot ID/checksum, dataset identity/version,
instrument, timeframe, available and selected coverage, counts, policy, warmup
and all three segments. The canonical envelope supports strict round-trip and
rejects missing/extra fields, unknown enums, unsupported versions and modified
hashes. Payload checksums use SHA-256. Plan and segment IDs use distinct
domain-separated SHA-256 namespaces, and every segment ID is bound to its plan
ID. Frozen contracts and the consuming service both revalidate all invariants.

The snapshot ID is never caller-chosen text. The temporal service uses the same
pure Phase 2C identity and contract validation used by snapshot creation and
`MarketDatasetReader`, including the canonical manifest path and exact ordered
partition set. It maps malformed snapshot/manifest fields to stable temporal
domain errors before accessing attributes or resolving a timeframe. Segment
indexes are likewise inseparable from their roles: only `0/TRAIN`,
`1/VALIDATION` and `2/TEST` are valid.

## Phase 4-03 reproducible experiment plans

`ExperimentPlanningService` joins one legitimate immutable snapshot and
manifest, one validated Phase 4-02 temporal plan, one factory-validated Phase
4-01 parameter space, an exact registered plugin and deterministic Phase 3A
backtest settings. It produces no execution result. Its complete versioned
manifest contains the nested canonical input documents, expanded combinations
and one immutable `PlannedRunSpec` for every combination multiplied by the
three temporal segments.

Planned specs use a single canonical order: combination index is primary and
segment index is secondary. Global indexes are contiguous. Snapshot/temporal,
plugin/search structure, cardinality and backtest configuration are prechecked
before factory expansion or spec construction. The default is 3,000 planned
runs and the conservative absolute ceiling is 30,000; no plan is truncated or
partially returned.

The top level records each complete normalized typed parameter document,
snapshot, temporal plan, plugin and common configuration once. A documented
spec contains compact canonical references to its combination and segment plus
its index, purpose, checksum and ID. The public object reconstructs and exposes
the retrospective context range, scored evaluation range, warmup and existing
`BacktestConfig`. The config reads the context range; the Phase 4-04 executor
excludes warmup observations from evaluation and metrics.

Plugin name/version and `engine_version` must already be safe tokens without
surrounding whitespace. The backtest schema must be a non-boolean integer in
the official supported Phase 3A set (currently 1 or 2). Nested combination
tuples and typed documents are revalidated by the shared Phase 4-01 boundary.

Roles have fixed purposes: TRAIN is `TRAINING`, VALIDATION is
`MODEL_SELECTION`, and TEST is `FINAL_HOLDOUT`. Only VALIDATION is eligible for
future model selection. The schema records `TEST_IS_FINAL_HOLDOUT`; this layer
does not compare metrics, rank parameters or select a winner.

SHA-256 checksums cover every spec and the complete manifest. Domain-separated
SHA-256 generates `experiment_id` and `run_spec_id`, binding all semantic
inputs, policies, ordering and limits. A `run_spec_id` identifies future work;
it is not the Phase 3A `run_id` of a completed backtest. The strict codec rejects
unknown schemas/enums, missing or extra fields, noncanonical UTC/Decimal data,
changed ordering/cardinality and altered hashes.

Planning is candle-free and side-effect-free: it does not call the engine,
publish artifacts, write `ADT_DATA_DIR`, connect to a database or network, or
start subprocesses/workers. Those responsibilities remain deferred to 4-04.

## Phase 4-04 context-aware experiment execution

The local executor revalidates the complete plan, snapshot, manifest, plugin
versions and limits before any strategy or engine is created. It then executes
exactly one planned spec at a time in canonical order. The default local limit
is 3,000 specs and the hard ceiling is 30,000; execution never materializes all
results in memory.

For planned runs, `BacktestConfig.data_range` is the read context and
`evaluation_range` is the only scored interval. Under
`WARMUP_OBSERVATION_ONLY`, candles before evaluation populate the bounded
strategy history but cannot submit orders, fill, charge fees, mark a portfolio
or create ledger/equity events. Lifecycle 1 is unchanged and contains
`on_start`, `on_candle`, `on_fill` and `on_end`. Lifecycle 2 adds one
`on_warmup_candle` callback per retrospective candle. Both versions are
officially supported, but positive warmup requires lifecycle 2 and its factory
must produce a callable callback before any strategy event or candle iteration;
zero warmup can execute a lifecycle 1 strategy with no such attribute. An
additional method never promotes lifecycle 1, and positive warmup is rejected
before `on_start` or candle reads even if the object exposes that method. Warmup
callbacks update strategy state with history ending at the current candle but
return no intents; `on_start` intents are held until the first evaluation open.
Candle indexes, orders, fills, trades and metrics are
evaluation-local. No candle at or after the evaluation end is exposed by the
bounded reader.

The built-in identities are versioned rather than reinterpreted in place:
`no-op@1` and `ema-cross-example@1` retain lifecycle 1 and remain compatible
with their existing search-space and plan documents when warmup is zero;
`no-op@2` and `ema-cross-example@2` declare lifecycle 2 and support positive
warmup. The registered descriptor is the lifecycle authority even when a
concrete Python object happens to expose an additional method. Textual plugin
version and lifecycle are separate protections. Planning copies the registered
lifecycle into each immutable `EvaluationBacktestConfig` and validates it
against the planned plugin reference; the existing `build_run_id()` then hashes
both the strategy descriptor and the explicit lifecycle. Consequently, even
custom plugins with the same name and textual version cannot reuse artifacts
across lifecycles.

Every new run uses the normal deterministic engine, artifact schema and atomic
Phase 3A store. The expected `run_id` includes the evaluation range. Existing
artifacts are reused only after `BacktestResultVerifier` authenticates their
config, snapshot, checksums, ledger, equity, trades and metrics. Corrupt or
incompatible artifacts are reported as a bounded failed record and are not
silently replaced.

New result manifests declare both `context_range` and `evaluation_range`, plus
the lifecycle actually executed; config and manifest must agree before an
artifact verifies. A legacy `BacktestConfig` retains its canonical bytes and
run IDs, and only its legacy manifest may omit lifecycle and infer version 1.
`data_range` denotes the evaluated interval. Readers accept legacy Phase 3A/3B
manifests by treating their former `data_range` as both ranges. Verification
rejects order lifecycle timestamps, fills, ledger entries, equity observations,
trades or metric periods outside evaluation, even when file and envelope
checksums have been recomputed.

Execution manifests use schema version 1 and explicit terminal records. Their
aggregate status is `COMPLETED`, `PARTIALLY_FAILED` or `FAILED`; the fixed
failure policy is `CONTINUE_AFTER_FAILURE`. Canonical IDs omit timestamps, and
atomic publication stores only the manifest plus its `COMMITTED` publication
record below the configured market root. Ranking, winner selection,
walk-forward, reports, concurrency, workers and distributed execution remain
deferred.

Before loading snapshot contracts or creating any engine/strategy, execution
computes a conservative canonical worst-case manifest size, including maximum
500-character errors and the final envelope, and rejects anything above 16 MiB.
Every terminal record is reconciled against its exact planned run, including
the direct `global_index = combination_index * 3 + segment_index` formula and
fixed TRAINING/MODEL_SELECTION/FINAL_HOLDOUT three-record group. Successful
records are bound to the legitimate snapshot: their expected run ID is
recalculated from the planned config, and their safe artifact path ends exactly
in that run ID. Publication validates the complete in-memory manifest before
path creation or I/O, verifies bounded PREPARED and COMMITTED staging contents,
then verifies the renamed target under the same lock. Reads reject a manifest
over 16 MiB or an oversized publication record before allocating their content.
The public verification frontier revalidates plan and snapshot and independently
verifies every COMPLETE/REUSED artifact and logical checksum; FAILED records
remain valid without artifact references.
Public execution-record and execution-manifest factories, together with their
payload helpers, validate enum, identifier, index, flag, optional-field,
collection and terminal-state types before accessing enum values or hashing.
Malformed in-memory inputs therefore fail with the execution-contract error
hierarchy before serialization, directory creation, staging or artifact I/O.

## Phase 4-05 rolling walk-forward

Walk-forward planning uses one immutable STRICT snapshot and the explicit
`ROLLING_FIXED_NON_OVERLAPPING_TEST` policy. The first complete fold begins
after any retrospective warmup, and every following fold advances exactly by
the TEST candle count. This makes consecutive TEST ranges adjacent and
non-overlapping while allowing data from an earlier TEST to become historical
TRAIN or VALIDATION data in a later fold. Incomplete trailing candles are
reported and ignored.
Plan validation independently recomputes snapshot candle count, first selected
boundary, exact fold count, every fold boundary, final consumed coverage and
trailing count from the official timeframe. Each embedded temporal plan must
carry the same TRAIN, VALIDATION, TEST and warmup counts as the window policy;
re-signing a contradictory plan does not make it valid.

Every fold is independently materialized through the existing temporal and
experiment planners and executed through `ExperimentExecutionService`. No
portfolio, strategy, indicator, order, ledger, reader or mutable cache is
carried between folds. Positive warmup continues to require lifecycle 2;
lifecycle 1 remains valid only when warmup is zero.

The initial selection policy requires an explicit existing comparison metric
and direction. Successful verified TRAIN is an eligibility requirement, while
only the corresponding VALIDATION metric supplies the score. Deterministic
ties use combination index and combination ID. The selection projection and
its hash contain no TEST reference, metric, status, checksum or path. A
separate immutable `FoldSelectionEvidence` projection records every eligible
or rejected candidate in canonical order, authenticates the full set with its
own checksum/ID, and is the only input accepted by ranking. The decision is
independently recomputed against that evidence.

The 4-04 executor may already have physically executed TEST for every planned
combination. The 4-05 selection boundary does not consult those TEST records or
artifacts. After the winning decision is frozen, only that combination's
`FINAL_HOLDOUT` record is reconciled with its planned spec, run ID, canonical
path and logical checksum and explicitly verified before metrics are loaded.
If it fails, the fold remains `FAILED_HOLDOUT` with the original decision; no
runner-up is selected and no other TEST result is consulted for fallback.

Compact canonical manifests are published atomically below
`market/optimization/walk-forward`. They contain ordered fold states,
experiment execution references, decisions and selected TEST metrics. They do
not aggregate fold performance. There is no 4-05 CLI; a safe consolidated
operator workflow remains deferred. Stability, overfitting and global
comparison reports belong to Phase 4-06.

Before serialization, publication, reuse or independent published verification,
the final manifest is reconciled fold-by-fold with the original plan and each
referenced 4-04 execution. Conservative byte calculators charge the complete
canonical search space against every possible run/candidate plus bounded
envelopes for folds, rejection evidence and holdout metrics before expansion.
A corrupt final target is removed under the plan lock and safely republished;
a valid identical target is reused and valid divergent content remains a
conflict.

## Phase 4-06 stability and overfitting controls

The stability service accepts only a Phase 4-05 execution that has passed an
independent semantic validator. Its policy must repeat the walk-forward
selection metric and direction and explicitly provide all thresholds. This
keeps the report reproducible and prevents an analyst from silently changing
the objective after observing TEST.

For each completed fold, Phase 4-06 compares the frozen winner's VALIDATION
score with that same winner's verified TEST score. Positive signed degradation
always means TEST became worse, regardless of whether the metric is maximized
or minimized. Failed folds remain in the denominator of the completion ratio
and receive no invented score. The report includes:

- exact completion and TEST-not-worse ratios;
- exact parameter transition and turnover ratios;
- VALIDATION, TEST and degradation minimum/median/maximum distributions;
- six explicit pass/fail controls;
- separate overfitting, parameter-stability and aggregate assessments.

Parameter turnover compares a domain-separated fingerprint of the canonical
parameter set. It does not compare fold-local combination IDs. TEST values from
non-selected candidates remain outside the analysis, and the report cannot
change a winner, select a runner-up or rank strategies globally.

`POSSIBLE_OVERFITTING` means one or more explicit degradation controls failed;
it is not a p-value, probability or proof. Advanced methods such as Deflated or
Probabilistic Sharpe Ratio, PBO, White's Reality Check, CPCV, purge/embargo,
Monte Carlo and sensitivity surfaces remain deferred. The report also makes no
paper-trading or production recommendation.

Canonical reports are bounded to 16 MiB and published under
`market/optimization/stability/<walk_forward_execution_id>/<report_id>` through
locked PREPARED/COMMITTED staging. Publication requires report recomputation
against the verified 4-05 source. Repeated publication reuses only identical
valid content; a corrupt target can be replaced under the same lock. There is
no 4-06 CLI.

## Deliberate limitations

Phase 4-01 adds only deterministic, finite parameter-search input contracts. It
does not call `DeterministicBacktestEngine`, read snapshots or publish result
artifacts. Every expanded combination contains a deterministic index, complete
normalized strategy parameters, the existing typed Phase 3C parameter document,
its SHA-256 checksum and a combination ID bound to the search-space ID.

The search-space schema is version 1. Its canonical JSON records the exact
plugin identity and schema/lifecycle versions, fixed and searchable parameters,
typed values, strict combination policy, cardinality and requested limit.
`Decimal` is encoded as canonical base-10 text without using the process-global
Decimal context; no `float` is introduced. Canonical output length is checked
before any zero padding, so an extreme exponent cannot trigger proportional
allocation. Canonical integers contain at most 128 magnitude digits, enforced
with exact integer bounds before string conversion. A SHA-256 checksum covers
the payload, while a domain-separated SHA-256 produces the deterministic
search-space ID.

The default expansion limit is 1,000 and the absolute ceiling is 100,000.
Cardinality is rejected before any combination or strategy instance is
materialized. Under `REJECT_SPACE`, one factory-invalid combination rejects the
entire space. Public frozen contracts enforce their invariants even when built
directly, and `expand()` independently rechecks schema, limits, exact
cardinality, checksum and ID before any factory call.

Phase 4-02 adds only temporal contracts. It does not read candles, execute a
backtest, expand parameter combinations or publish result artifacts. Experiment
manifests and execution, walk-forward analysis, purge/embargo windows,
optimization reports, multiple assets, frontend orchestration, schedulers,
paper trading and live trading remain outside this delivery.

## Paper replay boundary (Phase 5-03)

`DeterministicBacktestEngine.run()` retains terminal open-order cancellation as
its default behavior for historical backtests. Phase 5-03 invokes the same
engine with terminal cancellation disabled and with `force_close_at_end=false`.
This is required because the latest committed candle is a temporary observation
boundary rather than the end of a historical experiment. An order emitted on
that candle must remain open so a later complete replay can process it on the
next eligible candle.

Every paper cycle starts with a new strategy, engine, portfolio, ledger and risk
manager and replays the complete bounded RAW prefix. It does not resume mutable
Python objects. Lifecycle 1 accepts no warmup; lifecycle 2 receives only the
configured observation-only warmup before evaluation begins. The resulting
orders, fills, portfolio and risk-halt state are therefore governed by the same
contracts as Phase 3 and can be reproduced with the persisted source checksum.

The paper layer adds no performance claim and does not promote an optimized
strategy. It is a simulated execution substrate only. See
[`PAPER_TRADING.md`](./PAPER_TRADING.md) for persistence, commands and safety
limits.
