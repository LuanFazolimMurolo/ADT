# ML Predictions v1

## Purpose

This document defines the Phase 8-04 regression-only prediction boundary.

The project keeps these concepts separate:

- Target specification -> realized label
- Prediction specification -> model output prediction
- Recommendation -> trading action decision
- Execution -> operational authority outside the ML contract layer

## Target vs label vs prediction

A target specification describes the future return or value to be observed.

A realized label is the observed truth after the target matures.

A prediction is a model estimate created at the observation time for a future target value. It is not the label and cannot carry realized target dependency.

## Regression-only v1

The prediction problem type is restricted to:

- REGRESSION

No classification, probability, confidence, calibration, or recommendation semantics are part of the v1 contract.

## Logical prediction time

A prediction binds the original observation time as its logical prediction time:

- prediction_time == observation.observation_time
- feature_information_time <= prediction_time
- prediction_time < target_maturity(observation)

This keeps the prediction causally valid and prevents leakage from future target data.

## Replay semantics

Prediction identity is tied to the original observation and feature state, not to a later document creation time or wall-clock time. The document may be created during replay or backtest, but the logical time remains the original observation time.

## Model reference

The model reference is intentionally small and framework-independent.

Fields:

- model_id
- model_version
- artifact_checksum
- implementation

It intentionally excludes training datasets, metrics, registry state, champion/challenger status, or deployment semantics.

## Prediction specification

The prediction specification defines the regression task and binds to:

- problem_type = REGRESSION
- target specification
- feature set identity
- implementation provenance
- output kind = DECIMAL
- output unit matching target unit
- horizon identical to target.horizon_candles

It must not redefine a drifting horizon or inject recommendation semantics.

## Prediction output

The prediction output is a finite Decimal value.

Rules:

- Decimal only
- finite and non-NaN
- not infinity
- no bool, int, or string coercion
- no label required
- no realized target dependency stored in the prediction

## Logical identity vs document checksum

The logical prediction identity must be computed from the prediction inputs and provenance only:

- prediction specification
- model reference
- observation
- feature vector
- prediction_time

The predicted output value is excluded from logical identity.

The canonical document checksum includes the actual output value so that a different output for the same logical prediction is detectable as a checksum difference and reproducibility violation evidence.

## Decimal policy

All numeric outputs use the canonical Decimal representation already used by the ML document system. Non-finite values are rejected and the canonical encode/decode roundtrip remains strict and deterministic.

## Maturity and leakage boundary

The target maturity function remains authoritative. A prediction must always be created before the target becomes mature, ensuring no future leakage is embedded in the contract.

## Boundary rules

The prediction contract explicitly forbids:

- confidence
- probability
- calibration
- recommendation
- action semantics
- execution authority
- persistence or registry behavior
- inference engine logic

## Canonicalization and errors

The document architecture remains the same strict canonical document system used by the broader ML contracts. Unknown fields, duplicates, malformed Decimal values, missing fields, and invalid UTC timestamps are rejected in the canonical document validation path.
