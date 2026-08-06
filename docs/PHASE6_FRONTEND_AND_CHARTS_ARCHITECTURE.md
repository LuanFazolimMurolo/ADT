# Phase 6 — Advanced Frontend and Financial Charts Architecture

## 1. Status and authority

- **Phase**: 6
- **Delivery**: 6-01
- **Status**: architecture candidate for review
- **Base release**: annotated tag `phase-5`
- **Base commit**: `f50d8f087375158112f8d3f43dd25e45a5f83b05`
- **Scope authority**: this document and the Phase 6 section of `docs/ROADMAP.md`

Phase 6 begins only after the complete Phase 5 deterministic paper-trading
boundary. This delivery changes documentation only. It does not install a chart
library, add endpoints, create migrations, alter financial calculations or
start any remote operation.

## 2. Goal

Provide bounded and trustworthy visual analysis for market data, strategy
events and paper-trading performance while preserving these ADT properties:

- deterministic and reproducible calculations;
- closed-candle and UTC temporal semantics;
- Decimal financial values across persistence and API boundaries;
- authenticated and authorized administrative access;
- no long-running or network market-data work inside HTTP requests;
- auditable source identities, checksums and immutable historical evidence;
- explicit separation between observed data, calculated metrics and estimates.

## 3. Current capabilities inherited from Phase 5

The current system already provides:

- validated local RAW Binance Spot candles in Parquet;
- deterministic paper-session configuration and identity;
- complete replay through the Phase 3 engine;
- latest paper-session portfolio state;
- verified orders and fills;
- deterministic trade journal and closed-trade realization;
- daily, weekly and monthly realized period metrics;
- market-regime detection when explicitly configured;
- authenticated GET-only administrative paper-trading APIs;
- React administrative pages for session status, journal and period metrics.

These capabilities are not reimplemented in Phase 6.

## 4. Known data gap

The latest paper state contains current portfolio totals, but it is not a
historical mark-to-market series. Period metrics intentionally expose realized
accounting only.

Therefore, Phase 6 must not draw a historical equity or drawdown curve by
interpolating final state, summing realized PnL alone or inventing marks.

A correct portfolio timeline requires deterministic observations at explicit
closed-candle boundaries with at least:

- quote cash;
- base quantity;
- average entry price and cost basis;
- candle-close mark price;
- realized and unrealized PnL;
- total fees and slippage;
- equity;
- peak equity;
- drawdown and drawdown percentage;
- position and risk-halt state;
- session, candle-range, source and artifact checksums.

Phase 6-04 owns this missing contract.

## 5. Visualization data matrix

| Visualization | Existing authoritative source | Additional work |
|---|---|---|
| Candlesticks | Validated RAW closed candles | Bounded read-only HTTP projection |
| EMA and supported indicators | Deterministic indicator library | Chart projection bound to the same candle range |
| Orders and fills | Verified paper-session state | Chart annotation projection |
| Entry and exit markers | Deterministic trade journal | Stable event-to-candle mapping |
| Protective stops | Engine-owned paper orders/events | Explicit stop annotation contract |
| Realized daily/weekly/monthly PnL | Period metrics projection | Frontend chart only |
| Fees and slippage by period | Period metrics projection | Frontend chart only |
| Historical equity | Not currently persisted | Phase 6-04 portfolio timeline |
| Historical unrealized PnL | Not currently persisted | Phase 6-04 portfolio timeline |
| Historical drawdown | Not currently persisted | Phase 6-04 portfolio timeline |
| Heatmaps and comparisons | Partially available summaries | Bounded comparison projection |
| Regime overlay | Available only for compatible configured runs | Explicit optional overlay, never fabricated |

## 6. Chart rendering decision

The planned financial-chart renderer is **TradingView Lightweight Charts**,
subject to a dependency, license, attribution and bundle review in Phase 6-03.

Reasons:

- native candlestick and time-series rendering;
- TypeScript definitions;
- canvas-based rendering appropriate for bounded financial series;
- support for multiple series, markers and responsive resizing;
- narrower scope than a general dashboard-chart framework.

The exact package version must be pinned during implementation. The required
TradingView attribution notice must be preserved. No dependency is added by
Phase 6-01.

Non-financial tables and simple summaries should remain ordinary accessible
HTML. A chart must not be the sole representation of critical values.

## 7. Backend chart contracts

### 7.1 General rules

Every chart endpoint must:

- be GET-only in Phase 6;
- require the established authorization appropriate to its surface;
- read persisted ADT artifacts only;
- perform no Binance request, backfill, resampling, strategy execution or replay;
- use half-open UTC ranges `[start, end)`;
- return only closed candles and verified events;
- serialize financial Decimal values as strings;
- return stable canonical identities and source checksums;
- enforce explicit point, date-range and response-size limits;
- reject invalid or oversized requests rather than silently changing semantics;
- use deterministic chronological ordering;
- expose freshness and truncation state explicitly;
- avoid offset pagination over a growing time series.

### 7.2 Candle navigation

Phase 6-02 will define stable backward time navigation. The initial contract
target is:

- default page: at most 1,000 candles;
- absolute response ceiling: 5,000 candles;
- explicit pair and timeframe;
- explicit or cursor-derived UTC interval;
- no silent aggregation or downsampling;
- clients request a coarser timeframe or an older page when more history is
  required.

The exact OpenAPI schema is implementation work and is not frozen by this
architecture document.

### 7.3 Session annotations

Chart annotations must be derived only from verified session artifacts and must
carry stable IDs. At minimum:

- order ID and type;
- fill ID, side, quantity, execution price and time;
- trade/realization ID when available;
- protective-stop identity and state;
- fees and slippage;
- linkable session and journal identity.

Estimated targets, confidence or future horizons are not part of Phase 6.

## 8. Deterministic portfolio timeline

Phase 6-04 will extend the replay publication boundary with a separate,
content-addressed historical artifact.

Requirements:

1. Build observations from the same verified candle batch and engine events.
2. Use candle-close marks only; never use future candles.
3. Keep existing paper config IDs and latest-state compatibility.
4. Bind the artifact to:
   - session ID;
   - engine and lifecycle versions;
   - data range;
   - dataset version;
   - source checksum;
   - canonical artifact checksum.
5. Publish atomically.
6. Verify exact reconstruction independently.
7. Reject regression, tampering and incompatible schemas.
8. Bound observations by the existing session candle limit.
9. Avoid PostgreSQL migration unless a later reviewed operational requirement
   proves it necessary.

A chart endpoint may project this artifact but may not calculate a replacement
timeline ad hoc from incomplete summaries.

## 9. Frontend information architecture

### 9.1 Public surface

Route `/` remains public and must expose only intentionally public information.
No administrator session, private strategy configuration, operational control or
unreviewed performance data may leak into the public page.

### 9.2 Authenticated user surface

Phase 6-06 establishes `/app` as a distinct authenticated, read-only product
boundary. Initial scope may remain limited to the project owner. Public
self-registration is not enabled by Phase 6.

Candidate views:

- authorized market and session charts;
- authorized signals and trade history;
- performance summaries;
- notification preferences prepared for the later Telegram phase.

### 9.3 Administrator surface

`/admin` remains the operational and audit surface. Phase 6 adds visual analysis,
not process mutation.

Candidate routes:

- `/admin/market-data/charts`;
- `/admin/paper-trading/:sessionId`;
- `/admin/paper-trading/:sessionId/performance`.

Final route names remain an implementation decision.

## 10. Interaction and accessibility requirements

Every chart view must provide:

- textual title, instrument, timeframe and UTC range;
- visible loading, empty, stale and error states;
- keyboard-operable range and overlay controls;
- non-color-only buy/sell and positive/negative distinctions;
- accessible table or summary for critical chart data;
- reduced-motion compatibility;
- responsive behavior without horizontal page overflow;
- deterministic formatting for Decimal values and UTC timestamps;
- explicit timezone label instead of implicit browser-local interpretation.

## 11. Performance and safety budgets

Initial implementation targets:

- no unbounded arrays from HTTP;
- maximum 5,000 primary candle points per response;
- bounded annotations and portfolio observations tied to the returned range;
- request cancellation when the user changes filters;
- no duplicate polling requests;
- no full-page refresh for normal chart updates;
- chart resources disposed on component unmount;
- no secret, token or raw backend error included in chart payloads;
- no financial calculation delegated to JavaScript floating-point arithmetic
  when the backend is authoritative.

Budgets may become stricter after measurement. They may not be relaxed without
tests and documentation.

## 12. Testing strategy

### Backend

- UTC alignment and half-open interval tests;
- closed-candle-only tests;
- gap, tamper and checksum rejection;
- Decimal-string OpenAPI contract;
- stable ordering and cursor tests;
- point and response-size limit tests;
- authentication and authorization tests;
- proof that endpoint execution performs no network or strategy work;
- portfolio timeline reconstruction and independent verification.

### Frontend

- mapping from API strings to chart presentation without changing financial
  values;
- loading, empty, stale and error states;
- overlays and marker identity;
- route and filter synchronization;
- keyboard and accessible-name coverage;
- responsive resizing and cleanup;
- request cancellation and polling behavior;
- timezone labels and journal links.

### Integration

- validated candles through API into candlestick view;
- verified fill selected from journal and located on the chart;
- session update reflected without duplicate events;
- portfolio timeline checksum and source binding;
- non-admin and unauthenticated access rejection;
- no regression in existing Phase 5 pages.

## 13. Security boundary

Phase 6 introduces no exchange credentials and no real-capital path.

It must preserve:

- Supabase identity with FastAPI authorization;
- no trust in frontend role claims;
- bounded query validation;
- sanitized errors and request correlation;
- no filesystem paths in API responses;
- no Data API exposure of local paper artifacts;
- no secrets in source, logs, snapshots or generated frontend bundles;
- no chart action that starts, stops or mutates financial processes.

## 14. Explicitly deferred

- operational session and worker control: Phase 7;
- model training and strategy recommendation: Phase 8;
- persistent production hosting: Phase 9;
- Telegram-assisted signals: Phase 10;
- extended live validation: Phase 11;
- automatic real-capital trading: optional Phase 12;
- WebSocket streaming unless separately justified by measured polling limits;
- cross-quote currency conversion;
- estimates presented as guaranteed future prices or execution times.

## 15. Phase 6-01 completion criteria

Phase 6-01 is complete only when:

- the revised roadmap and this document agree;
- only the two intended documentation files changed;
- Markdown and repository diff checks pass;
- the staged diff is audited before commit;
- no code, dependency, migration, secret or remote state changed;
- the architecture is reviewed against existing Phase 5 contracts;
- the next implementation delivery is explicitly selected;
- commit, push and merge occur only after the user supplies successful gate
  output.
