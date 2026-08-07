# Phase 6-03 Instrument Chart

## Status

This document records the first internal increment of Phase 6-03. The Phase
6-03 delivery remains open until verified paper-session annotations and
journal-to-chart navigation are integrated.

## Surface

Protected administrator route:

```text
/admin/paper-trading/chart
```

The page reads the bounded administrator candle API delivered in Phase 6-02.
It cannot start collectors, mutate paper sessions, call Binance, place orders
or write market data.

## Dependency decision

The renderer is `lightweight-charts` pinned to version `5.2.0`.

The page preserves the TradingView attribution logo. Critical candle values
remain available as ordinary HTML so the canvas is not the only
representation.

## Bounded data behavior

- initial page: 1,000 closed candles;
- stable backward navigation through `next_before`;
- maximum locally loaded candles: 5,000;
- deduplication by canonical `open_time`;
- deterministic replacement if the dataset version changes;
- polling every 30 seconds;
- no offset pagination, WebSocket, resampling or silent downsampling.

## EMA semantics

The engine EMA is implemented with:

- close values known at candle close;
- arithmetic-mean seed over the first period;
- alpha `2 / (period + 1)`;
- fixed Decimal precision 50 with half-even rounding.

The chart's EMA 9/21 lines mirror the seed and recurrence but are explicitly
visual projections over the currently loaded candles. Lightweight Charts and
browser rendering use IEEE-754 numbers, so the overlay is not an authoritative
engine artifact and must not be used for accounting or execution.

A later reviewed backend projection would be required to publish exact
engine-aligned Decimal indicator values over arbitrary historical windows.

## Remaining Phase 6-03 work

- session selection and verified session identity;
- bounded order and fill annotations;
- entry and exit classification from the deterministic journal;
- protective-stop annotations;
- chart-to-journal and journal-to-chart navigation;
- session-aware EMA periods when the strategy contract supports them;
- accessibility and browser integration gate.

## Verified annotation projection

The internal Phase 6-03B1 backend increment adds an administrator-only endpoint:

```text
GET /api/v1/admin/paper-trading/sessions/{session_id}/chart-annotations
```

Required query parameters are `start` and `before`, forming one half-open UTC
interval `[start, before)`. The explicit annotation ceiling defaults to 1,000
and cannot exceed 5,000.

The projection:

- loads one verified local paper-session config and latest state;
- performs no replay, Binance request or state mutation;
- returns order creations and fills only inside the requested interval;
- classifies fills as `ENTRY` or `EXIT` through the deterministic trade journal;
- marks only the reserved `engine-stop-loss` client tag as an engine-managed
  protective stop;
- binds output to config, state, dataset and source checksums;
- rejects an interval whose combined orders and fills exceed the requested
  limit instead of truncating it;
- exposes compatible EMA periods only for the verified
  `ema-cross-example` strategy contract.

Frontend marker rendering and journal navigation remain part of the next
internal Phase 6-03B2 increment.

## Frontend session integration

The internal Phase 6-03B2 increment connects the verified annotation endpoint
to the protected chart page.

The page now:

- lists up to 100 dashboard sessions for explicit selection while preserving
  manual SHA-256 entry;
- binds the selected session to its canonical pair and timeframe;
- reloads the complete annotation projection whenever the bounded candle
  interval changes;
- renders verified entry, exit and engine-managed protective-stop markers;
- uses EMA periods from the persisted `ema-cross-example` strategy contract;
- hides EMA overlays for selected strategies that publish no compatible EMA
  periods;
- preserves instrument-only EMA 9/21 as a clearly visual projection;
- highlights a trade selected through the `trade_id` URL parameter;
- exposes the latest 100 annotations as ordinary HTML so the canvas is not the
  only representation;
- links dashboard sessions and journal trades to the chart;
- links the chart back to the journal with the selected session identity.

Phase 6-03 remains a closure candidate until the combined backend/frontend
gate, staged audit and manual browser verification pass.
