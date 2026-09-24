# ML Feature Engineering v1

This document defines the Phase 8-03 feature-engineering vocabulary that is intentionally narrow and deterministic.

## Scope

This gate establishes a minimal static catalog for deterministic, leakage-safe feature recipes. It does not introduce fitted preprocessing, model state, persistent cache, feature DAGs, or cross-timeframe features.

## Canonical direct-source rules

- All feature recipes are derived from authenticated candle data.
- There is no feature-to-feature DAG in v1.
- The same canonical indicator math is reused for raw and derived features.
- Feature dependencies remain exact and causal.
- Warmup values remain unavailable as None.
- There is no silent imputation, zero-fill, or forward-fill.

## v1 recipe families

Raw:
- open
- high
- low
- close
- volume

Indicators:
- ema
- rsi
- atr
- bollinger_middle
- bollinger_upper
- bollinger_lower
- macd
- macd_signal
- macd_histogram

Derived deterministic recipes:
- simple_return
- candle_body
- candle_range
- upper_wick
- lower_wick
- close_vs_ema_ratio
- atr_ratio
- bollinger_width
- bollinger_position

## Exact derived formulas

- simple_return = current_close / baseline_close - 1
- candle_body = close - open
- candle_range = high - low
- upper_wick = high - max(open, close)
- lower_wick = min(open, close) - low
- close_vs_ema_ratio = close / ema(period) - 1
- atr_ratio = atr(period) / close
- bollinger_width = (upper - lower) / middle
- bollinger_position = (close - lower) / (upper - lower)

All arithmetic must remain Decimal-based and finite. Zero denominators are rejected explicitly.

## Missingness and leakage

- Warmup remains None.
- Missing numeric results are rejected unless the source contract explicitly allows unavailable values.
- No fitted normalization or target encoding is permitted.
- No cross-asset or cross-timeframe features are supported in v1.
- No regime feature is included in this catalog.

## Training and serving skew

The same recipe must produce the same value in dataset materialization, evaluation, and future execution. The canonical feature executor must remain the same across all future phases.

## Boundaries

Phase 8-03 Gate 1 is intentionally a vocabulary and validation foundation only. It does not add persistence, model training, execution authority, or operational runtime behavior.
