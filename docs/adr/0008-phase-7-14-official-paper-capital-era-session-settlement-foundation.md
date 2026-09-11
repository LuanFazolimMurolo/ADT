# ADR 0008 — Phase 7-14 Official Paper Capital Era & Session Settlement Foundation

Status: Accepted

Date: 2026-09-11

## Context

Phase 7 now has one remaining capital/session roadmap boundary before the
future ADT Official Portfolio can be designed honestly.

Phase 1 already owns simulated financial bookkeeping through:

- `simulation_runs`;
- append-only `capital_movements`.

`simulation_runs` owns simulated-capital source identity and lifecycle.

`capital_movements` is the authoritative append-only cash ledger.

Phase 7-08 explicitly preserves that ledger as the only gross simulated-capital
authority.

`OperationalPaperCapitalAuthorization` reserves capital from one exact
`simulation_id`.

Phase 7-09 materializes that authority into an immutable local
`PaperSessionConfig`.

Phase 7-10 grants execution eligibility.

Phase 7-11 creates durable run epochs that already freeze:

- activation identity;
- materialization identity;
- capital-authorization binding;
- profile and mandate bindings;
- `simulation_id`;
- deterministic `session_id`;
- exact `config_checksum`.

No second capital ledger may be introduced by Phase 7-14.

## Decision summary

Phase 7-14 introduces two related foundations:

1. explicit designation of one eligible Phase 1 simulation as an official paper
   capital era; and
2. exactly-once financial settlement of one completed operational paper session
   into the existing append-only capital ledger.

It does not create another capital ledger.

## Official paper capital era

A generic `simulation_run` does not automatically become official ADT capital.

Phase 7-14 introduces durable `OperationalPaperCapitalEra` metadata that binds
one exact `simulation_id` to future official-paper-capital history.

The era has its own stable `era_id`, but it does not duplicate the simulation
balance.

The simulation remains authoritative for current balance, lifecycle,
initial capital, currency and timestamps.

### Designation requirements

An era may be designated only when the bound simulation:

- exists;
- is `ACTIVE`;
- is not already bound to another official era;
- has exactly its canonical `INITIAL_CAPITAL` opening movement;
- has no later financial movements;
- has no existing operational paper capital authorization;
- has no materialization, activation or run history.

This prevents historical test or generic simulations from being
retroactively described as official ADT performance.

At most one currently ACTIVE designated official era may exist.

## Capital-era reset model

A reset never rewrites an existing era.

A later reset creates a new simulation and a new official era.

The prior simulation, ledger movements, settlements and era designation remain
immutable historical evidence.

Historical performance must remain independently inspectable across resets.

Phase 7-14 does not create the future public era-comparison UI.

## Session-to-capital provenance

No new session identity layer is introduced.

The existing run epoch already freezes the authoritative chain through:

- `simulation_id`;
- deterministic `session_id`;
- `config_checksum`;
- activation;
- materialization;
- authorization;
- profile;
- mandate.

Settlement must reuse this exact provenance.

## Settlement aggregate

Phase 7-14 introduces immutable terminal
`OperationalPaperSessionSettlement` evidence.

At most one settlement may exist per deterministic `session_id`.

A settlement binds one exact official capital era, simulation, run epoch,
authorization, local session state and persisted timeline evidence.

The settlement record is terminal and cannot be reopened or rewritten.

## Settlement eligibility

Settlement is fail-closed.

The run epoch's `simulation_id` must belong to a designated
`OperationalPaperCapitalEra`.

The exact run epoch and immutable specification must verify.

Settlement requires:

```text
desired_state = STOPPED
observed_state = STOPPED
```

A `FAILED` epoch is not settlement eligible.

The canonical local `PaperSessionConfig` and final `PaperSessionState` must be
loaded from the authoritative paper-session store.

Their `session_id` and `config_checksum` must match the frozen run provenance.

The state must pass the existing config/state verification contract.

Browser-provided PnL or financial state is never authoritative.

The final state must also verify through the existing
`PaperPersistedStateBinding`.

That binding must match the exact session, config, state, dataset, source and
persisted timeline identities used by settlement.

Settlement requires a flat final portfolio:

```text
base_quantity = 0
cost_basis = 0
average_entry_price = 0
unrealized_pnl = 0
```

A STOPPED session with an open position is not settlement eligible.

Runner STOP does not imply liquidation.

The paper contract keeps `force_close_at_end = false`.

Phase 7-14 must not invent a forced-close trade to make a session settleable.

## Settlement arithmetic

For a verified flat final portfolio:

```text
settlement_delta = final_quote_cash - initial_capital
settlement_delta = realized_pnl
```

Both equalities must verify exactly with `Decimal`.

Float conversion is forbidden for authoritative settlement arithmetic.

## Fees and slippage

The existing portfolio accounting already includes fill fees in economic
cost and proceeds.

Therefore final `realized_pnl` already contains the economic fee effect.

`total_fees` and `total_slippage_cost` remain audit evidence only.

Settlement must not post `realized_pnl` plus a separate `FEE` movement.

Doing so would double-count fees.

## Ledger posting

For a non-zero settlement delta:

```text
settlement_delta > 0 -> TRADE_PROFIT
settlement_delta < 0 -> TRADE_LOSS
```

For `settlement_delta = 0`, no ledger movement is inserted.

The settlement row is still created as terminal financial evidence.

## Exactly-once settlement

Settlement uniqueness must be enforced by PostgreSQL for deterministic
`session_id`.

The command uses an actor-scoped idempotency key and deterministic intent
fingerprint.

Same actor/key/fingerprint is an exact replay.

Same actor/key with a different fingerprint is a conflict.

Settlement is committed atomically.

For non-zero PnL, one transaction must contain the reservation release,
one ledger movement and immutable settlement evidence.

A concurrent duplicate settlement must never create two capital movements.

Database uniqueness and transaction rollback are the final race-condition
backstop.

## Capital authorization consumption

Normal settlement requires the bound authorization to remain `AUTHORIZED`.

Within the same PostgreSQL transaction and simulation financial mutex,
settlement consumes the reservation through:

```text
AUTHORIZED -> REVOKED
```

The revocation transition records the settlement actor and timestamp.

Reservation release and the optional PnL ledger movement must commit atomically.

If the authorization was already revoked outside normal settlement,
the initial Phase 7-14 contract fails closed.

Exceptional manual reconciliation remains separately reviewed future scope.

## Session financial finality

A successfully settled `session_id` is financially terminal.

The operational START contract must reject creation of any future run epoch
for an already settled `session_id`.

Historical run epochs remain preserved.

## Official-era terminalization protection

A designated official simulation must not terminalize while it has:

- an `AUTHORIZED` operational capital reservation;
- a non-terminal operational paper run epoch; or
- run history for a distinct `session_id` without settlement.

A failed or otherwise financially ambiguous session therefore blocks clean
official-era closure.

Phase 7-14 must not silently discard or rewrite that unresolved financial
state.

Resolution requires a separately reviewed future reconciliation contract.

## Transaction boundaries

Canonical local filesystem verification occurs before the PostgreSQL
settlement transaction begins.

No database transaction may remain open during long-running local replay,
filesystem verification or network work.

After local evidence verifies, the settlement transaction must:

1. lock the bound simulation financial mutex;
2. revalidate official-era identity and settlement uniqueness;
3. revalidate terminal run-epoch identity and the bound authorization;
4. consume/release the authorization;
5. insert the optional single ledger movement;
6. insert immutable settlement evidence; and
7. commit atomically.

All mutable PostgreSQL authority is revalidated while the financial mutex is held.

## Authority split

PostgreSQL owns official-era designation, settlement identity, operational
provenance, exactly-once settlement and linkage to the existing capital ledger.

The local paper filesystem remains authoritative for canonical config, state,
orders, fills and persisted paper evidence.

PostgreSQL must not become a duplicate paper-trading event ledger.

## HTTP and administration boundary

Phase 7-14 may expose bounded authenticated administrator operations for era
designation, settlement eligibility inspection and explicit settlement.

HTTP must not run the paper engine or mutate canonical local paper state.

The browser must not provide authoritative PnL, balance or settlement amounts.

The backend resolves authoritative provenance and settlement arithmetic.

## Frontend and security boundary

A hand-written administration UI is not required for the Phase 7-14 foundation.

If a UI is later justified, it remains administrator-only.

New Phase 7-14 tables require a reviewed PostgreSQL migration with RLS enabled.

Browser/Data API roles receive no operational write authority.

Administrative mutations occur through authenticated FastAPI and direct PostgreSQL access.

## Future Official Portfolio boundary

Phase 7-14 establishes authoritative inputs for a future ADT Official Portfolio:

- explicit official capital eras;
- immutable official session settlement evidence; and
- exactly-once linkage into the existing authoritative capital ledger.

It does not implement the ADT Official Portfolio product.

Multi-session aggregation, public equity curves and public portfolio presentation remain future work.

## Trading-horizon boundary

Trading-horizon labels remain deferred.

Concepts such as day trade and swing trade still have no enforceable domain semantics.

Phase 7-14 does not add a cosmetic trading-horizon field.

## Explicitly out of scope

Phase 7-14 does not add:

- a second capital ledger;
- customer, subscriber or administrator real-money custody;
- exchange credentials or live exchange orders;
- the public ADT Official Portfolio;
- multi-session public portfolio aggregation;
- forced liquidation at runner STOP;
- settlement of open positions or FAILED epochs;
- trading-horizon labels;
- Telegram, billing, subscriptions or machine learning.

## Gate plan

Implementation proceeds through the following reviewed gates:

- Gate 2A — pure era/settlement domain contracts;
- Gate 2B — reviewed PostgreSQL persistence and migration;
- Gate 2C — repositories, locking, exactly-once settlement and era guards;
- Gate 2D — application service and local-evidence verification;
- Gate 2E — settled-session START prevention and simulation terminalization protection;
- Gate 3 — bounded administrator HTTP transport and generated API contract;
- Gate 4 — skipped unless a hand-written UI becomes justified;
- Gate 5 — integrated validation, migration reconciliation, docs and pure-FF closure.

## Consequences

Phase 7-14 creates the financial bridge between the operational paper runner
and the existing authoritative append-only simulated-capital ledger.

It preserves one financial source of truth while adding explicit official-era
identity and exactly-once terminal session settlement evidence.

The result is suitable as a foundation for a later reviewed ADT Official
Portfolio aggregation contract without claiming that product is implemented.

Trading-horizon labels remain an independent deferred Phase 7 deliverable.
