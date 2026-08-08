# Paper-trading performance visualizations

## Purpose

This document defines the Phase 6-05 read-only visualization boundary for ADT
paper trading.

The delivery converts already verified and persisted ADT financial evidence into
bounded administrator-facing charts. It does not create an execution path,
replay strategies inside HTTP, query an exchange, mutate paper sessions or
replace authoritative Decimal accounting with browser arithmetic.

## Authoritative sources

Phase 6-05 uses two different historical authorities and keeps their semantics
separate.

### Mark-to-market portfolio timeline

Historical equity, unrealized PnL and drawdown come only from the immutable
portfolio timeline published by Phase 6-04.

The read path is:

1. load the canonical paper-session config and current state;
2. resolve the immutable `state_checksum -> timeline_id` sidecar;
3. load and verify the content-addressed portfolio timeline artifact;
4. validate that session, config, state, dataset and source bindings match;
5. project a bounded chronological page.

The HTTP boundary does not reconstruct the timeline from current summaries and
does not execute a strategy.

### Calendar-period realized metrics

Daily, ISO-weekly and Gregorian-monthly realized visualizations reuse the
existing verified period-metrics service.

These series represent realization events only:

- realized PnL;
- realized fees;
- realized slippage;
- win/loss/breakeven counts;
- profit factor;
- realization activity.

They do not represent historical equity, unrealized PnL or drawdown.

## Portfolio timeline API

Administrator-only endpoint:

```text
GET /api/v1/admin/paper-trading/sessions/{session_id}/portfolio-timeline
```

Query parameters:

- `before`: optional exclusive UTC-aligned backward cursor;
- `limit`: optional point limit, default 1,000 and maximum 5,000.

The response:

- is GET-only and administrator-authorized;
- returns Decimal financial values as strings;
- exposes session/config/state/dataset/source identities;
- exposes timeline and page content checksums;
- uses deterministic chronological ordering;
- returns explicit `has_more_before` and `next_before`;
- never silently downsamples or aggregates observations.

Integrity headers:

```text
X-ADT-Paper-Timeline-ID
X-ADT-Paper-Timeline-State-Checksum
X-ADT-Paper-Timeline-Content-Checksum
X-ADT-Paper-Timeline-Rows
```

## Immutable state-to-timeline reference

Phase 6-05 adds an immutable sidecar keyed by the exact paper-state checksum:

```text
market/paper-trading/<session_id>/portfolio-timeline-refs/<state_checksum>.json
```

The reference binds:

- session ID;
- config checksum;
- state ID;
- state checksum;
- dataset version;
- source checksum;
- timeline ID;
- timeline content checksum.

The sidecar is checksummed and atomically published. Existing references are
validated rather than overwritten with divergent content.

Legacy states whose timeline artifact predates the sidecar fail closed on the
read path. A later normal `UPDATED` or `NOOP` paper cycle can idempotently
materialize the missing reference without changing the persisted state identity.

## Historical performance page

Protected route:

```text
/admin/paper-trading/performance
```

The page exposes, for one selected session:

- historical equity curve;
- current and maximum drawdown over the loaded slice;
- cumulative realized PnL;
- cumulative unrealized PnL;
- cumulative fees;
- cumulative slippage;
- exact Decimal summary cards;
- a textual table of recent persisted observations;
- provenance checksums;
- links to the market chart and trade journal.

Chart coordinates convert API Decimal strings to JavaScript numbers only for
rendering. Exact financial values remain the backend-owned strings and are used
for textual presentation.

The page requests at most 5,000 observations. If older observations exist, the
UI states that the visible slice is truncated.

## Session comparison

The historical performance page may load at most two timelines:

- one primary session;
- one optional comparison session.

Each timeline remains independently bounded to 5,000 observations.

Nominal equity overlays are permitted only when both sessions have the same
`quote_asset`. The frontend never invents cross-currency conversion.

Drawdown comparison is percentage-based and retains each session's independently
verified timeline.

The comparison table keeps exact textual values available in addition to the
chart.

## Realized-period charts

The existing protected route:

```text
/admin/paper-trading/period-metrics
```

retains its deterministic calendar filters and now provides chart
representations for:

- realized PnL by DAILY, WEEKLY or MONTHLY bucket;
- realized fees and slippage by bucket;
- profit factor when defined;
- realization activity;
- win/loss/breakeven distribution.

The UI labels this surface `realized-only` and explicitly states that it is not
historical mark-to-market performance.

## Bounded heatmap

The period-metrics visualization includes a realized-PnL heatmap.

Properties:

- maximum 366 visible calendar buckets;
- most recent buckets are retained when the source series is larger;
- truncation is explicitly announced;
- no silent aggregation or downsampling;
- color is not the sole representation of critical values;
- each cell exposes an accessible textual label with period, exact realized PnL
  and realization count;
- the complete authoritative bucket table remains available below the charts.

Heatmap intensity is only a visual normalization against the maximum absolute
realized PnL in the visible heatmap slice. It is not a financial metric.

## Accessibility and presentation

The visualization surfaces preserve:

- explicit UTC labels;
- loading, empty and error states;
- textual headings and descriptions;
- exact-value cards and tables;
- non-color textual win/loss labels;
- accessible heatmap cell labels;
- responsive layouts;
- chart disposal on unmount;
- TradingView Lightweight Charts attribution.

## Performance bounds

Phase 6-05 keeps these limits:

- portfolio timeline page: default 1,000, maximum 5,000 observations;
- primary historical chart: maximum 5,000 observations;
- optional comparison chart: maximum 5,000 observations;
- heatmap: maximum 366 visible calendar buckets;
- period-metrics backend: existing maximum 5,000 calendar buckets.

No endpoint or frontend view introduces an unbounded array.

## Security and nonclaims

Phase 6-05 does not:

- create, start, pause or delete paper sessions;
- call Binance or another exchange;
- replay a strategy in HTTP;
- mutate a paper state;
- add PostgreSQL persistence or migrations;
- add a new dependency;
- perform cross-currency conversion;
- derive historical equity from realized PnL;
- silently downsample financial history;
- expose administrator data publicly.

The backend authorization boundary remains authoritative.

## Verification

The delivery is covered by:

- portfolio timeline read-service pagination and tamper tests;
- immutable sidecar lookup/backfill/tamper tests;
- administrator HTTP authorization and OpenAPI tests;
- TypeScript client tests;
- exact-string-to-visual-projection tests;
- historical performance page tests;
- realized-period projection tests;
- bounded heatmap tests;
- same-currency and cross-currency comparison tests;
- full backend and frontend gates before integration.
