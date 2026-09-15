# Phase 7-15 Reviewed Trading-Horizon Labels Completion Evidence

Date: 2026-09-14

## Purpose

This record closes Phase 7-15 — Reviewed Trading-Horizon Labels and the final
open roadmap deliverable of Phase 7 — Operational Control Plane.

The delivery adds durable administrator-reviewed `DAY_TRADE` and `SWING_TRADE`
classification to operational paper-session identity without deriving a label
from timeframe and without introducing forced holding-period behavior.

## Governing architecture decision

The governing architecture record is:

`docs/adr/0009-phase-7-15-reviewed-trading-horizon-labels.md`

The reviewed horizon remains approved profile authority and identity-bearing
metadata. It is not an executable holding-period policy.

## Git integration evidence

Starting remote `main` baseline:

`326bb9fa7056f320b58e37e2cded5382d71779d7`

Integrated implementation milestone:

`17bfaec88be5aa5c5b3c116ed016c63603b4465d`

Integrated tree:

`4574eb7dad5b84c70478a093d655747aa184bf72`

The feature branch:

`feat/phase-7-15-reviewed-trading-horizon-labels`

was published at the exact integrated milestone.

Before integration it was independently verified as six commits ahead of the
starting `main`, zero commits behind, with the starting `main` as merge base.

Remote `main` was advanced by pure fast-forward.

No merge commit, squash or force push was used.

After publication, remote `main` and the feature branch were verified
identical.

No GitHub combined commit statuses were attached to the integrated milestone.
Closure authority therefore comes from the reviewed local validation, Git/ref
verification and Supabase parity evidence recorded here.

## Delivered contract

Phase 7-15 delivers:

- explicit `TradingHorizon` values `DAY_TRADE` and `SWING_TRADE`;
- operational paper-session profile schema version `2`;
- mandatory reviewed `trading_horizon` for new schema-v2 profiles;
- preserved schema-v1 profiles with no inferred or synthetic label;
- PostgreSQL persistence as nullable `trading_horizon text`;
- database enforcement of v1 -> `NULL`;
- database enforcement of v2 -> `DAY_TRADE` or `SWING_TRADE`;
- `PaperSessionConfig` schema version `3`;
- horizon participation in new config checksum and session identity;
- exact propagation through profile materialization;
- administrator API create/replace/read transport;
- explicit administrator frontend selection;
- legacy UI projection as unclassified rather than silently backfilled.

The reviewed horizon does not by itself alter paper-engine trading behavior.

## Legacy compatibility

Existing operational profile schema version `1` remains valid and unlabeled.

Existing `PaperSessionConfig` schema versions `1` and `2` remain supported.

Frozen legacy-v2 evidence remained unchanged:

Config checksum:

`a4a9badade5f151a4836c2ddc2ec587892c5312d79982c0bce917623e1dc4a4a`

Session id:

`601b9016b9957e6660557ad33c1421dfe012c345b130c480a23d50cff20d8bb7`

New schema-v3 identities differ when only the reviewed trading horizon differs.

## PostgreSQL migration

Phase 7-15 owns:

`supabase/migrations/20260914000000_phase_7_15_reviewed_trading_horizon_labels.sql`

Frozen SHA-256:

`ab43929dbab0ec0b363ef18a921c6a42f4fe1a2e8c47a764a083b085a4f1c1f7`

Supabase CLI `2.117.0` was used against the linked ADT project.

Before publication, `20260914000000` was proved to be the only pending remote
migration.

The reviewed migration was then applied successfully.

After publication:

- migration history showed `20260914000000` Local = Remote;
- final dry-run reported `Remote database is up to date.`;
- linked migration history was synchronized through `20260914000000`.

Direct remote schema inspection confirmed:

- table `public.operational_paper_session_profile_revisions`;
- column `trading_horizon`;
- data type `text`;
- nullable column shape;
- schema version `1` requires `trading_horizon IS NULL`;
- schema version `2` requires `trading_horizon IS NOT NULL`;
- schema version `2` accepts only `DAY_TRADE` or `SWING_TRADE`.

## Validation evidence

### Backend

Final full backend validation:

- `4947 passed`;
- `1 skipped`;
- `3 warnings`;
- `88%` total coverage.

Integrated horizon regression:

- `393 passed`.

Additional backend validation:

- Ruff check: PASS;
- MyPy: PASS in `289` source files;
- `pip check`: PASS.

### Ruff format baseline exception

Full-tree `ruff format --check` identified two pre-existing files:

- `services/backend/app/operational_paper_capital_eras/domain.py`
- `services/backend/tests/test_operational_paper_capital_eras_domain.py`

The exception was formally classified as pre-existing because:

- both blobs were identical to the starting `origin/main`;
- Phase 7-15 never touched either file;
- all 15 Python files changed by Phase 7-15 passed `ruff format --check`;
- all 15 Phase 7-15 Python files passed `ruff check`.

No unrelated formatting churn was introduced.

### Frontend

Final frontend evidence passed:

- generated OpenAPI contract check;
- ESLint;
- application TypeScript typecheck;
- E2E TypeScript typecheck;
- `32/32` Vitest files;
- `277/277` Vitest tests;
- production build;
- bundle-budget check;
- `56/56` Playwright tests.

The earlier integrated frontend smoke passed `72/72` tests.

Existing non-fatal React `act(...)`, TypeScript parser compatibility and Vite
chunk-size warnings did not cause validation failures.

## Gate closure

- Gate 1 — contract: PASS / CLOSED
- Gate 2A — domain: PASS / CLOSED
- Gate 2B — persistence: PASS / CLOSED
- Gate 2C — executable config identity: PASS / CLOSED
- Gate 3A — administrator API: PASS / CLOSED
- Gate 3B — administrator frontend: PASS / CLOSED
- Gate 4 — integrated compatibility: PASS / CLOSED
- Gate 5 — validation, migration parity, completion evidence, roadmap/handoff
  closure and pure fast-forward publication: PASS / CLOSED

## Explicitly out of scope

Phase 7-15 does not implement:

- automatic timeframe-to-horizon inference;
- forced end-of-day liquidation;
- maximum holding-period enforcement;
- exchange-session calendars;
- automatic strategy recommendation or promotion;
- ADT Confidence Score;
- ADT Official Portfolio aggregation;
- machine learning;
- Telegram distribution;
- subscriber entitlements or billing;
- exchange credentials;
- live orders or fills;
- automated real-capital execution.

## Completion decision

The implementation, compatibility guarantees, remote migration state,
administrator transport, integrated validation and pure fast-forward
publication satisfy ADR 0009 and the roadmap scope.

Phase 7-15 is therefore classified:

`COMPLETE / CLOSED / INTEGRATED INTO main`

Phase 7-15 was the only remaining Phase 7 roadmap deliverable.

Phase 7 — Operational Control Plane is therefore classified:

`COMPLETE / CLOSED`

The roadmap dependency required before Phase 8 is now satisfied.

Phase 8 itself has not been started or selected for implementation by this
closure record.

The ADT Official Portfolio and real-capital execution remain future scope.
