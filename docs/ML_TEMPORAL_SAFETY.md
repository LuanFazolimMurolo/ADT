# ML Temporal Safety v1

## Purpose

This document defines the Phase 8-05 fit-scope boundary as a deterministic, read-only permission contract. It does not fit a model, materialize weights, train a preprocessor, or create runtime execution authority.

MLFitScope is intentionally not a training engine. It is a checkpoint that proves a candidate row belongs to the TRAIN split and therefore may serve as evidence for future fit-related validation.

## Leakage taxonomy

The ML domain distinguishes among several leakage families:

- role leakage: a row belongs to VALIDATION or TEST but is presented as TRAIN evidence
- target leakage: the future target range crosses beyond the allowed role coverage
- cutoff leakage: target information time occurs after the split training cutoff
- retrospective context leakage: historical feature dependencies are earlier than the authorized training window
- lineage leakage: a row or document mixes incompatible dataset/snapshot identity

The existing MLTemporalSplit contract remains authoritative for these checks.

## TRAIN semantics

A fit-scope contract is intrinsically TRAIN-only. The fit permission is derived from the underlying split:

- fit role: TRAIN
- fit coverage: split.train
- fit cutoff: split.training_cutoff
- dataset lineage: split.dataset
- retrospective policy: split.allow_retrospective_context

No separate role, dataset, cutoff, or retrospective-state field may exist in MLFitScope. The derived semantics come from MLTemporalSplit without duplication.

## VALIDATION and TEST semantics

VALIDATION and TEST rows are never fit-authorized. The fit-scope validator rejects them before any feature or label logic is accepted.

This prevents accidental leakage during walk-forward evaluation or future hyperparameter selection. The contract remains narrow: it only authorizes train-scope evidence for a future fit workflow, while leaving evaluation and final holdout isolation to the existing split boundary.

## Training cutoff as authoritative boundary

The split training cutoff defines the latest target information time allowed for a legal TRAIN label. A TRAIN label remains acceptable only when:

- the label target future range is wholly inside the TRAIN segment, and
- label.target_information_time <= split.training_cutoff

This is the canonical boundary used by MLTemporalSplit.validate_row for TRAIN rows.

## MLFitScope as a TRAIN-only permission boundary

MLFitScope validates whether a canonical row and label pair may be considered TRAIN fit evidence. It does not perform any training, scoring, normalization, or optimizer logic.

It is minimal and deterministic:

- it holds only the split reference;
- it derives role, dataset, coverage, cutoff, and history policy from the split;
- it validates that the row is canonical and TRAIN-local;
- it enforces the existing target and cutoff rules through the authoritative split validator.

## Causal retrospective feature history

Feature dependencies may be historical, but only if they remain consistent with the canonical split semantics. When retrospective context is enabled, the existing MLTemporalSplit policy permits historical dependency ranges that remain causally valid within the authorized TRAIN context.

When retrospective context is disabled, the split-level validation must reject disallowed historical context before a fit-scope row can be accepted.

## Purge and embargo

This gate does not introduce a manual purge object, purge width, purge candles, or embargo abstraction.

The effective purge behavior is the existing target-crossing and training-cutoff logic already encoded in MLTemporalSplit and MLLabel. There are no new operational states to configure or observe here.

## Overlapping labels

Overlapping TRAIN labels are legal when each target range remains inside the same TRAIN segment and does not cross the cutoff. Overlap alone is not leakage unless it violates the canonical split or target-time semantics.

## Canonical lineage

Every MLFitScope instance inherits its dataset and snapshot lineage from the split. There is exactly one dataset/snapshot lineage per fit scope, and the document codec validates that lineage via the existing canonical contract system.

## Future constraints not yet implemented

The following remain future scope and are explicitly out of this gate:

- fitted preprocessing and learned normalization
- feature-selection search and model selection
- hyperparameter optimization
- model cache and persisted fitted state
- distributional training artifacts
- runtime execution authority

These are not implemented here and must not be inferred as present.

## Final TEST isolation and walk-forward compatibility

The final TEST segment remains isolated. MLFitScope cannot authorize TEST or VALIDATION rows, and split policy remains the single source of truth for walk-forward compatibility.

This provides a clean future boundary for any later fitted-state or evaluation phases: the fit permission remains TRAIN-local, while future evaluation remains segmented and role-local.
