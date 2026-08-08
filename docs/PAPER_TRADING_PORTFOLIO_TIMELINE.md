# Paper-Trading Deterministic Portfolio Timeline

## 1. Status and scope

Phase 6-04 closes the historical mark-to-market gap identified by the Phase 6
architecture. The implementation adds a separate immutable portfolio timeline
for a verified local paper-trading replay.

It does **not** change paper-session identity, latest-state schema, strategy
behavior, execution rules, risk rules, market-data ingestion, PostgreSQL
schema, or public/authenticated-user authorization.

No HTTP endpoint is added by Phase 6-04. A bounded read-only projection may be
added by the performance-visualization delivery, but it must read this persisted
artifact and must not reconstruct a substitute series from summaries.

## 2. Authoritative inputs

One timeline is reconstructed from all of the following, which must agree:

- the immutable `PaperSessionConfig`;
- the verified `PaperSessionState`;
- the exact closed-candle `PaperCandleBatch` used for that state;
- the state's deterministic fills;
- the existing Decimal-only portfolio accounting and drawdown risk rules.

The source candle batch must match the state's data range, dataset version and
source checksum. Candle continuity and canonical candle serialization are
revalidated before an observation is accepted.

## 3. Observation semantics

There is exactly one observation per evaluated closed candle, bounded by the
existing session `max_candles` contract.

Each observation records:

- session/config/state identity;
- dataset version and source checksum;
- evaluated candle index;
- candle open and close timestamps;
- close mark price;
- quote cash and base quantity;
- average entry price and cost basis;
- realized and unrealized PnL;
- cumulative fees and slippage cost;
- equity and peak equity;
- drawdown and drawdown percentage;
- risk-halt state.

Fills are applied at their verified engine candle boundary, then the portfolio
is marked to that candle's close. Paper trading already forbids
`force_close_at_end`, so no synthetic terminal close is needed.

The final reconstructed portfolio and risk-halt state must equal the verified
latest paper state exactly. A divergence fails closed.

## 4. Content identity

The semantic timeline payload is canonicalized with the existing ADT canonical
JSON rules.

`timeline_id` is:

```text
SHA256("adt-paper-portfolio-timeline-v1\0" + canonical semantic payload)
```

`content_checksum` additionally binds that payload to the resulting
`timeline_id`.

Operational publication timestamps are intentionally excluded from semantic
identity.

## 5. Persistence layout

Artifacts are published under the persistent market-data root:

```text
paper-trading/<session_id>/portfolio-timelines/<timeline_id>/
├── manifest.json
└── observations.parquet
```

`observations.parquet` stores all financial Decimals losslessly as strings and
uses the project's existing PyArrow/Snappy stack.

`manifest.json` binds the artifact to:

- session, config, state and state checksum;
- engine and strategy lifecycle versions;
- base/quote assets and timeframe;
- dataset version and source checksum;
- context and evaluation ranges;
- initial capital and candle count;
- timeline ID and semantic content checksum;
- Parquet filename, byte size and SHA-256 file checksum.

## 6. Atomic publication and immutability

Publication uses a content-addressed target and a dedicated timeline lock.

A new artifact is first written to a temporary sibling directory. Files and the
staging directory are fsynced, then the completed directory is promoted with
`os.replace`, followed by an fsync of the parent directory.

An already-existing `timeline_id` is never overwritten. It is loaded and fully
verified; exact equality makes publication idempotent, while divergence or
corruption fails closed.

## 7. Replay integration

For an `UPDATED` paper cycle:

1. replay produces the verified candidate state;
2. the portfolio timeline is reconstructed from the same batch and candidate
   state;
3. the immutable timeline artifact is published and re-read;
4. only then is the latest `state.json` publication attempted.

For a `NOOP` cycle, the same deterministic timeline can be materialized
idempotently if the content-addressed artifact is absent, without changing the
latest state.

This ordering prevents a newly published latest state from claiming a replay
prefix whose Phase 6-04 timeline failed to persist.

## 8. Verification and corruption behavior

`paper-trading verify` continues to rebuild the session through the
deterministic engine and compare the exact state. It then independently
reconstructs the expected portfolio timeline and loads the corresponding
persisted content-addressed artifact.

Verification rejects, among other cases:

- candle/source identity divergence;
- invalid or noncanonical observations;
- final accounting divergence;
- manifest checksum or schema divergence;
- Parquet byte-size/checksum divergence;
- Parquet row-count divergence;
- semantic timeline ID/content-checksum divergence;
- persisted timeline mismatch against exact reconstruction.

Persistence-layer corruption is translated to the existing
`PaperSessionVerificationError` at the service verification boundary.

## 9. Compatibility and nonclaims

Phase 6-04 preserves existing paper config/session IDs and latest-state
documents. No migration or new dependency is introduced.

Historical values are authoritative only at evaluated closed-candle
boundaries. This artifact does not claim:

- intrabar equity;
- live exchange-account balances;
- cross-asset currency conversion;
- leverage or short accounting;
- future estimates, targets or confidence;
- arbitrary interpolation between candle closes.

The timeline is the authoritative Phase 6 source for later historical equity,
unrealized-PnL and drawdown visualizations.
