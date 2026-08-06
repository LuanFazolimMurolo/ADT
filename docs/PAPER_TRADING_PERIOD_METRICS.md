# Paper-trading calendar-period performance metrics

## Purpose

This document defines the read-only calendar-period performance projection for
ADT paper trading. The projection is deterministic, auditable and derived only
from canonical, independently verified paper-session state and trade-journal
executions.

The feature does not execute strategies, submit orders, mutate sessions, access
exchange accounts or introduce a long-running task inside FastAPI.

## Authoritative inputs

For every matched paper session, the service:

1. loads and verifies the immutable session configuration;
2. loads and verifies the latest canonical paper-session state;
3. reconstructs the deterministic trade journal;
4. replays each `SELL` realization using the same average-cost accounting
   contract verified against the final portfolio;
5. binds the result to the exact config checksum, state ID and state checksum.

Sessions whose configured `quote_asset` differs from the requested quote asset
are excluded. Nominal values from different quote currencies are never added together.

## Realization accounting

Calendar attribution occurs at each exit execution's `event_time`. A trade with
partial exits in different periods is therefore distributed across those
periods instead of being assigned in full to its final `closed_at`.

For each `SELL` realization:

- entry notional, entry fees and entry slippage are tracked while the position
  is opened or increased;
- the proportional entry notional, entry fees and entry slippage are released
  according to the sold quantity;
- released cost basis is released entry notional plus released entry fees;
- realized PnL is exit notional minus exit fee minus released cost basis;
- realized fees are allocated entry fees plus the exit fee;
- realized slippage is allocated entry slippage plus exit slippage.

The sum of reconstructed realization PnL for a session must match the verified
journal's total realized PnL. A mismatch fails closed.

## Calendar contract

All boundaries use UTC and half-open intervals `[start, end)`.

- `DAILY`: UTC calendar day beginning at `00:00`;
- `WEEKLY`: ISO week beginning Monday at `00:00` UTC;
- `MONTHLY`: Gregorian month beginning on day 1 at `00:00` UTC.

`period_from` and `period_before` must be aligned to the selected granularity.
The bounded interval includes empty calendar buckets, so the returned series is
continuous and deterministic even when no exit realization occurred.

## Metrics

Each bucket and the aggregate totals expose:

- period start and exclusive end;
- quote asset;
- realization count;
- winning, losing and breakeven realization counts;
- contributing session and symbol counts;
- exit notional;
- released cost basis;
- realized fees and realized slippage;
- gross profit and gross loss;
- realized PnL;
- win rate;
- profit factor.

`win_rate_pct` is null when the bucket has no realizations. `profit_factor` is
null when gross loss is zero, rather than inventing an infinite JSON value.

## Deterministic identity

The service returns:

- a query checksum over the canonical filters and granularity;
- a content checksum over the exact source bindings, continuous bucket series
  and aggregate totals.

Equal verified inputs and equal filters produce equal checksums and content.

## Backend API

Authenticated administrators can query:

```text
GET /api/v1/admin/paper-trading/period-metrics
```

Required query parameters:

- `quote_asset`;
- `period_from`;
- `period_before`.

Optional parameters:

- `granularity=DAILY|WEEKLY|MONTHLY`;
- `session_id`;
- `base_asset`;
- `timeframe`;
- `strategy_name`;
- `strategy_version`.

The endpoint is GET-only and depends on the administrator authorization
boundary. It returns the query and content checksums in the JSON body and in:

```text
X-ADT-Period-Metrics-Query-Checksum
X-ADT-Period-Metrics-Content-Checksum
```

The implementation is bounded to at most 5,000 calendar buckets, 100,000
realizations and 10,000 matched source states.

## Administrative frontend

The protected route is:

```text
/admin/paper-trading/period-metrics
```

The page provides explicit UTC date-time bounds, quote-asset isolation,
daily/weekly/monthly selection, optional session/asset/strategy filters,
aggregate cards, a continuous bucket table and provenance checksums.

The page states the accounting boundary prominently so realized activity is not
presented as historical mark-to-market performance.

## Explicit nonclaims

The historical series does not claim or derive:

- historical unrealized PnL;
- historical equity;
- portfolio return by period;
- drawdown by period;
- Sharpe, Sortino or CAGR;
- mark-to-market performance;
- currency-converted cross-asset portfolio performance.

The latest verified paper-session snapshot may still expose current equity,
unrealized PnL and drawdown through the existing live dashboard. Those snapshot
values are separate from this historical realization series.

## Operational and safety properties

- no network access is required to reconstruct the series from persisted state;
- no API request executes a strategy or changes execution/risk state;
- no database migration is introduced;
- no CSV or mutable report is used as an authority;
- all monetary arithmetic remains Decimal-based;
- malformed, inconsistent or unverifiable state fails closed;
- the feature preserves closed-candle and no-look-ahead execution semantics.
