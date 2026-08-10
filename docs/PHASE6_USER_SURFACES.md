# Phase 6-06 Public and Authenticated User Surfaces

## Status and scope

Phase 6-06 defines the public, authenticated and administrative presentation
boundaries. It adds only bounded read projections and frontend views. It creates
no registration, financial mutation, paper-session ownership model, migration
or exchange integration.

## Surface boundaries

| Surface | Routes | Authority |
|---|---|---|
| Public | `/` | Intentional public projections only |
| Authenticated | `/app/*` | Valid Supabase identity confirmed by FastAPI |
| Administrative | `/admin/*` | Valid identity plus backend `app_admins` membership |

`/app` and `/admin` are different boundaries. A valid authenticated session may
have `is_admin=false` and continue using the non-administrative application.
Public self-registration is disabled; the landing page links only to `/login`.

The public landing may call only:

- `GET /api/v1/system/status`;
- `GET /api/v1/public/simulation`.

Neither projection exposes administrative, audit, movement, paper-session or
strategy data.

## Authentication lifecycle

Supabase supplies the identity token. FastAPI validates it and
`GET /api/v1/app/me` returns the verified `user_id` plus `is_admin` resolved
exclusively from `public.app_admins`. Frontend identity data is a navigation aid,
not authorization for a backend resource.

- 401 means missing, invalid or expired authentication. A persistent 401 removes
  local access and returns the user to login.
- 403 means the user remains authenticated but lacks authorization. It does not
  destroy a valid Supabase session.

`AuthenticatedRoute` protects `/app`. The administrative `ProtectedRoute` also
requires the confirmed administrator flag, while every administrative API still
enforces backend `require_administrator` independently.

## Paper-session reader policy

ADT currently has no persisted `user_id → paper_session_id` ownership relation.
For the Phase 6 MVP, a project-owner reader is an authenticated UUID present in
`app_admins`.

- any authenticated user may use `/app` and read persisted market candles;
- a non-project-owner receives `200` with an empty paper-session catalog;
- a project-owner reader may read all local paper sessions through `/app`;
- session-scoped endpoints authorize before resolving paper services,
  repositories or artifacts;
- an unauthorized user receives the same 403 for an existing or nonexistent
  session ID, so an ID is neither a capability nor a secret.

This policy is a temporary project-owner read boundary, not per-user ownership
or row-level ACL.

## HTTP inventory

All Phase 6-06 application endpoints are GET-only:

| Area | Endpoint | Access and bounds |
|---|---|---|
| Identity | `/api/v1/app/me` | Any authenticated user |
| Market | `/api/v1/app/market-data/candles/{base}/{quote}` | Any authenticated user; persisted closed RAW candles; default 1,000, maximum 5,000 |
| Paper catalog | `/api/v1/app/paper-trading/sessions` | Authenticated; non-owner receives zero items; `page_size` maximum 100 |
| Session detail | `/api/v1/app/paper-trading/sessions/{session_id}` | Project-owner reader |
| Annotations | `/api/v1/app/paper-trading/sessions/{session_id}/chart-annotations` | Project-owner reader; half-open UTC range; maximum 5,000 |
| Trades | `/api/v1/app/paper-trading/sessions/{session_id}/trades` | Project-owner reader; `page_size` maximum 100 |
| Timeline | `/api/v1/app/paper-trading/sessions/{session_id}/portfolio-timeline` | Project-owner reader; persisted artifact; default 1,000, maximum 5,000 |
| Period metrics | `/api/v1/app/paper-trading/sessions/{session_id}/period-metrics` | Project-owner reader; exactly the path session and its configured quote asset |

The boundary has no POST, PATCH or DELETE route and cannot mutate a collector,
runner, paper session, setting, simulation or capital movement.

## Financial and data contracts

Candles are local persisted closed RAW observations. An HTTP read performs no
Binance request, source fetch, resampling or repair. Results are bounded and use
backward temporal pagination without silent downsampling.

Financial `Decimal` values remain base-10 strings through JSON and browser
presentation. Timestamps and half-open ranges are explicit UTC. The frontend
formats backend values but does not reconstruct accounting with JavaScript
floating-point arithmetic.

The portfolio timeline is a persisted content-addressed artifact bound to the
session, source range and checksums. HTTP never reconstructs it from latest
state. Historical equity, unrealized PnL and drawdown come from this timeline.
Period metrics are realized-only and do not claim historical mark-to-market.
The `/app` boundary has no cross-session period query or session comparison.

## Signals

Trading signals do not yet have an authoritative contract or artifact. Orders,
fills, trades and annotations retain their own meanings and are not presented as
signals. Signal views are deliberately deferred to a future reviewed delivery
that defines authoritative creation, identity, persistence and verification.

## Presentation and testing contract

Authenticated views provide explicit loading, empty, forbidden and error states;
keyboard-operable labeled controls; UTC text; non-color-only labels; and textual
tables or summaries for critical chart values. Layout CSS covers narrow screens,
while bounded table containers handle wide tabular data without intentional page
overflow.

Automated backend tests cover authentication, project-owner policy, empty
non-owner catalogs, pre-lookup anti-IDOR behavior, bounds, Decimal serialization,
GET-only OpenAPI contracts and persisted-artifact reads. Vitest covers routes,
safe 401/403 lifecycle, states, controls, UTC/Decimal presentation, pagination
and binding rejection. Playwright exercises public, authentication, market,
paper catalog, detail and performance flows with deny-by-default network mocks.

The local automated browser closure covers Chromium, responsive/mobile overflow,
keyboard operation, reduced motion, journal↔chart navigation, native control
patterns and authentication boundaries. Its final stability evidence is 47/47
Playwright tests in two consecutive full runs across 12 specs, plus 30/30 for
the `phase6-accessibility` project with `repeat-each=3`.

This does not claim complete WCAG or cross-browser validation. Manual validation
remains for a real screen reader, contrast, 200%/400% zoom, physical
touch/orientation and complete visual-clipping inspection. Firefox, WebKit and
other operating-system/rendering combinations were not validated by this local
closure.

## Known limitations and next delivery

- there is no per-user paper-session ownership or ACL;
- public registration remains disabled;
- signals remain deferred;
- `/app` is read-only and cannot operate workers or financial state;
- the production bundle is classified as **DEBT WITH HARD BASELINE BUDGET**;
  route-level code splitting remains future optimization work even though the
  deterministic Phase 6 budget passes;
- cross-quote conversion and cross-session authenticated comparisons are absent.

Phase 6-06 is complete. Phase 6 remains in progress: the Phase 6-07E candidate
audit consolidates integrity, browser/accessibility, performance and security
evidence in [`PHASE6_CLOSURE.md`](./PHASE6_CLOSURE.md), while Phase 6-07F still
owns the final full gate, staged-diff audit, approved integration and annotated
Phase 6 tag.
