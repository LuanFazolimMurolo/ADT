# Market Candle Chart API

## Status

- Phase: 6
- Delivery: 6-02
- Surface: administrator-only read API
- Persistence: transactionally cataloged local RAW Parquet
- Network behavior: no exchange request is permitted during HTTP execution

## Endpoint

```http
GET /api/v1/admin/market-data/candles/{base_asset}/{quote_asset}
```

Required query parameter:

- `timeframe`: one configured canonical timeframe.

Optional query parameters:

- `before`: an exclusive UTC candle boundary;
- `limit`: page size from 1 through 5,000; default 1,000.

The first request omits `before` and returns the latest closed page. When
`has_more_before` is true, the next request sends `next_before` as `before`.

## Temporal contract

All ranges are UTC and half-open:

```text
[range_start, range_end)
```

Every returned candle opens inside that interval. `range_end` is also the
exclusive cursor boundary. A supplied cursor must be UTC and aligned to the
selected timeframe.

The endpoint does not silently round cursors, fill gaps, resample candles,
truncate an oversized request or return open candles.

## Response identity

The response includes:

- canonical exchange, market type, pair and timeframe;
- requested and effective temporal boundaries;
- full available catalog coverage;
- requested limit and returned count;
- total cataloged candle count;
- logical dataset version and version algorithm;
- canonical page content checksum;
- backward-pagination state;
- ordered closed OHLCV candles.

Financial values are JSON strings backed by Decimal values. JavaScript clients
must not convert them to binary floating point for authoritative accounting.

## Integrity

A request acquires the dataset lock, recovers any pending transaction for that
dataset and reads the cataloged version. Only intersecting monthly partitions
are loaded.

The content checksum binds:

- query and effective range;
- dataset identity and logical version;
- pagination state;
- canonical logical bytes of every returned candle.

Any missing interval, duplicate, incompatible identity, open candle, malformed
catalog boundary or checksum inconsistency fails closed.

## HTTP headers

Successful responses include:

- `Cache-Control: no-store`;
- `X-ADT-Candle-Dataset-Version`;
- `X-ADT-Candle-Content-Checksum`;
- `X-ADT-Candle-Rows`;
- `X-Request-ID`.

## Security boundary

The route requires the established administrator authorization dependency.
Frontend role claims are not trusted. Responses expose no filesystem path,
credential, token or raw exception.

The route is GET-only and cannot:

- fetch from Binance;
- create or update datasets;
- start a collector, worker, backtest or paper runner;
- mutate a session;
- access an exchange account;
- place a real or simulated order.

## Defensive bounds

- default page: 1,000 candles;
- absolute page ceiling: 5,000 candles;
- no offset pagination;
- no silent aggregation or downsampling;
- exact configured timeframe registry only.

Larger visual ranges must use stable backward pages or a deliberately coarser
timeframe.

## Verification

The delivery is covered by:

- service-level deterministic pagination tests;
- catalog and gap rejection;
- administrator authentication tests;
- Decimal-string response tests;
- header and OpenAPI contract tests;
- generated TypeScript contract freshness;
- Ruff, Mypy and Pytest repository gates.
