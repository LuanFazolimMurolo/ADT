# Phase 6 closure candidate

**Status: CANDIDATE — LOCAL 6-07F GATE PASSED; RELEASE CLOSURE PENDING**

## A. Scope and status

This document is the operational closure record for the local Phase 6
candidate. It consolidates implementation and validation evidence through the
full local Phase 6-07F gate. It does not mark Phase 6 complete or assert remote
CI or deployment. Exact staged-diff approval, the approved commit and
integration, and the annotated `phase-6` tag remain required for formal
completion.

The governing scope remains the [roadmap](./ROADMAP.md) and the
[Phase 6 architecture](./PHASE6_FRONTEND_AND_CHARTS_ARCHITECTURE.md).

## B. Implemented Phase 6 deliveries

- **6-01** defined visual architecture, chart-data and security boundaries.
- **6-02** delivered the bounded, local, read-only
  [candle API](./MARKET_CANDLE_CHART_API.md).
- **6-03** delivered instrument and paper-session charts with verified
  annotations and journal navigation, as described in the
  [instrument chart contract](./PHASE6_03_INSTRUMENT_CHART.md).
- **6-04** delivered the deterministic, content-addressed
  [portfolio timeline](./PAPER_TRADING_PORTFOLIO_TIMELINE.md).
- **6-05** delivered bounded [performance visualizations](./PAPER_TRADING_PERFORMANCE_VISUALIZATIONS.md)
  and [period metrics](./PAPER_TRADING_PERIOD_METRICS.md).
- **6-06** delivered separated public, authenticated `/app` and administrative
  [user surfaces](./PHASE6_USER_SURFACES.md).
- **6-07A–E closure work** covers reconnaissance, integrity closure,
  browser/accessibility validation, deterministic size budgets and the local
  security/auth/OpenAPI/documentation audit.
- **6-07F local gate** passed the final backend, frontend and browser validation
  matrix. Exact staged-diff approval, integration and release tagging remain
  pending.

## C. Integrity closure

### B1R — RAW partition integrity

RAW datasets have a partition-integrity manifest v1. Each binding includes the
dataset version, canonical relative partition path and SHA-256 digest. The
writer publishes the version and manifest coherently. HTTP candle reads verify
only the partitions touched by the bounded query; they do not perform a full
logical RAW scan, network fetch, repair, resampling or backfill.

A legacy dataset without the manifest fails closed at the HTTP boundary.
Backfill is an explicit offline operation and is never automatic in an HTTP
request.

### B2R — persisted paper-state binding

Derived paper read models do not trust only a re-signable latest-state file.
Their external historical binding uses the portfolio timeline reference and
timeline manifest. The metadata-only verifier used by annotations, journal and
period metrics does not read observations Parquet, consult RAW, execute replay
or invoke the engine. Full timeline reads retain complete artifact validation.

Missing or invalid bindings fail closed. The current RAW `dataset_version` is
not treated as historical authority for persisted paper state.

## D. Browser and accessibility closure

Local Chromium automation covers responsive/mobile overflow, keyboard
operation, reduced motion, journal↔chart navigation, native control patterns
and authentication boundaries. The stability evidence is 12 specs and 47/47
Playwright tests in each of two consecutive full runs. The focused
`phase6-accessibility` project passed 30/30 with `repeat-each=3`.

This is automated local evidence, not a complete WCAG or cross-browser claim.
The remaining manual matrix is listed in section I.

The final local Phase 6-07F gate has now passed. Backend evidence is 2,218
passed and 1 skipped with 87% total coverage. Frontend evidence is Vitest 25
files / 175 passed, successful typecheck, E2E typecheck, lint and OpenAPI
contract checks, a 129-module production build, and 47/47 Playwright tests.
Formal integration and the annotated `phase-6` release tag remain pending.

## E. Performance budgets

### Deterministic response-size budgets

These are hard regression ceilings over canonical maximum fixtures, not runtime
middleware response limits:

| Response | Hard ceiling |
| --- | ---: |
| Candles, admin | 1,638,400 B |
| Candles, app | 1,638,400 B |
| Annotations, admin | 2,621,440 B |
| Annotations, app | 1,376,256 B |
| Timeline, admin | 3,145,728 B |
| Timeline, app | 3,145,728 B |
| Journal page, admin | 262,144 B |
| Journal page, app | 65,536 B |
| Period metrics, app | 3,145,728 B |
| Period metrics, admin combined | 8,388,608 B |

The admin period fixture simultaneously contains 5,000 buckets and 10,000
`source_states`. Its observed candidate response is 7,511,068 B against the
single combined 8 MiB ceiling; the arrays do not have independent response
budgets.

The D2/D2R regression evidence is 56 targeted tests in 8 files, including 5
response-budget tests. This is not a frozen full-backend-suite count.

### Journal export limitation

Journal export is cardinality-bounded to 10,000 trades, but trade count alone
cannot define a universal serialized-byte maximum because executions per trade
also contribute. A stress fixture with 10,000 trades and 100,000 executions
measured about 26,879,602 B as CSV and 71,533,792 B as JSONL.

Classification: **DEFERRED HARD BYTE BUDGET**. The rejected 8 MiB CSV and
24 MiB JSONL candidates are not Phase 6 budgets. Export remains bounded by
cardinality contracts without a universal hard serialized-byte regression
ceiling in Phase 6.

### Frontend bundle

Classification: **DEBT WITH HARD BASELINE BUDGET**.

| Asset measure | Hard ceiling | Current deterministic baseline |
| --- | ---: | ---: |
| Total JavaScript raw | 819,200 B | 722,859 B |
| Total JavaScript gzip | 245,760 B | 208,717 B |
| Largest JavaScript raw | 819,200 B | 722,859 B |
| Largest JavaScript gzip | 245,760 B | 208,717 B |
| Total CSS raw | 57,344 B | 43,764 B |
| Total CSS gzip | 12,288 B | 8,100 B |

The Vite warning for a chunk above 500 kB is tracked debt, not by itself a
Phase 6 blocker. Route-level code splitting remains a future optimization.

### Report-only measurements

Latency, new-process latency, B1R verification timing, RSS, `tracemalloc`,
browser heap and render latency are report-only observations. They are not SLAs
or hard CI gates. Development-machine ASGI latency was unsuitable for a hard
gate because of an observed Python 3.13 test-environment anomaly.

On the Python 3.13 workstation, a pre-existing wake-up/hang affected some
synchronous/ASGI tests. An ephemeral external heartbeat was used for selected
local gates. No workaround exists in the repository or application; it is not a
production requirement and did not affect response-byte or hash measurements.

## F. Authentication, authorization and security boundaries

### Route classes

- **Public** routes expose intentional read-only projections. The Phase 6 public
  landing uses only system status and the public simulation projection. The
  inherited Phase 5 paper runner/session/fill/order projections remain
  intentionally public and read-only under their documented Phase 5 contract;
  they are not `/app` authorization proxies.
- **Authenticated `/app`** routes are GET-only. Any authenticated identity may
  read bounded market candles. A non-administrator paper-session catalog is
  neutral and empty. Session-scoped detail, annotations, trades, timeline and
  period metrics require the project-owner reader policy or administrator
  membership.
- **Administrative** routes retain the administrator dependency at the FastAPI
  boundary.

FastAPI dependencies, not frontend routing, are the authorization authority.
Generic authenticated identity and administrator/project-owner policies remain
distinct. Session authorization runs before resource lookup, so existing and
nonexistent identifiers are indistinguishable to an unauthorized user and both
produce 403. A valid 403 does not invalidate the session; 401 follows the
refresh/invalidation lifecycle.

The `/app` frontend and backend do not proxy administrative routers.
Internally shared read services are acceptable only after the intended
dependency authorizes the request.

### Read-only and source-work guarantees

Phase 6 presentation endpoints do not create paper sessions, submit orders,
start or stop runners, pause workers, mutate strategies or simulations, move
capital, or call an exchange. Market candle GETs read only persisted local RAW
partitions and perform no Binance request, resampling, repair, backfill or
strategy execution. Derived paper reads perform no replay, engine execution or
RAW reconstruction.

API error contracts sanitize authentication, authorization, integrity,
validation and availability failures. RAW integrity failures and invalid paper
state bindings expose generic conflict/error contracts, including sanitized
409 responses, without internal checksums, filesystem paths, raw exceptions,
stack traces or secrets.

Local RAW and paper artifacts are filesystem artifacts and are not exposed
through the Supabase Data API. Phase 6 introduces no migration or dependency.

## G. OpenAPI and generated-contract status

The local OpenAPI generation check has zero drift. The `/api/v1/app` inventory
contains only GET operations, and its response schemas contain no local
filesystem path, secret, credential or private-token field. Generated frontend
API code must be regenerated from the backend contract, never edited manually.

## H. Known limitations and deferred items

- Per-user paper-session ownership/ACL is absent; the current MVP reader is a
  project owner represented by administrator membership.
- Manual accessibility and additional renderers remain unvalidated (section I).
- The journal export hard byte ceiling is deferred, although cardinality is
  bounded.
- Route-level bundle splitting remains technical debt.
- Orders, fills, trades and annotations retain their precise meanings and are
  not relabelled as authoritative trading signals. Signals remain deferred
  until a reviewed authoritative contract exists.
- Cross-quote conversion and WebSocket delivery remain deferred unless a later
  phase justifies and specifies them.
- Phase 7 owns operational session/worker controls; Phase 8 owns ML, training
  and recommendation; Phase 9 owns persistent production hosting; Phase 10 owns
  Telegram-assisted signals; Phase 11 owns extended live validation; optional
  Phase 12 owns any reviewed real-capital trading path.

## I. Manual validation remaining

The following were not claimed by the local automated closure:

- real screen-reader operation;
- contrast review;
- browser zoom at 200% and 400%;
- physical touch and orientation changes;
- complete visual-clipping inspection;
- Firefox, WebKit and other operating-system/rendering combinations.

These items do not automatically block the local implementation candidate
unless the roadmap or the final 6-07F review changes the acceptance boundary.

## J. Final 6-07F checklist

- [x] Re-run the full backend suite and backend quality gates.
- [x] Re-run the full frontend unit, type, lint, OpenAPI, build and bundle gates.
- [x] Re-run the required browser closure matrix.
- [x] Confirm deterministic integrity and response/bundle budget evidence.
- [x] Audit working-tree secrets, local paths, migrations and dependencies.
- [ ] Audit and approve the complete staged diff and exact file inventory.
- [ ] Commit and verify the approved Phase 6 closure candidate.
- [ ] Integrate the candidate into `main` by verified fast-forward.
- [ ] Create and verify the annotated `phase-6` tag only after remote `main`
  points to the approved closure commit.
- Remote CI, deployment and hosted-service validation are not claimed unless
  independently executed and evidenced.

## K. Formal completion conditions

Phase 6 becomes formally complete only after Phase 6-07F passes its full local
gate and staged audit, the approved integration is performed, and the annotated
Phase 6 tag is created as directed. Until all of those conditions are met, the
authoritative status is **IN PROGRESS — 6-07F RELEASE CLOSURE PENDING**.
