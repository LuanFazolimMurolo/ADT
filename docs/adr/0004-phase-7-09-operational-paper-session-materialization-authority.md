# ADR 0004 — Phase 7-09 Operational Paper Session Materialization Authority

- **Status**: Accepted — Gate 1
- **Date**: 2026-08-31
- **Scope**: Phase 7-09

## Context

Phase 7-07 created the approved `OperationalPaperSessionProfile`.
Phase 7-08 created the authoritative
`OperationalPaperCapitalAuthorization`.

The existing paper subsystem already owns immutable `PaperSessionConfig`,
`paper_config_checksum(config)`, deterministic `paper_session_id(config)` and
atomic/idempotent local `config.json` publication.

`PaperSessionConfig` intentionally contains replay semantics, not operational
UUIDs or administrator provenance.

## Decision

Phase 7-09 introduces durable PostgreSQL
`OperationalPaperSessionMaterialization` provenance while preserving the
existing local `PaperSessionConfig` and `session_id` authorities.

## Architecture

The materialization authority binds an approved profile and an AUTHORIZED capital authorization to the existing canonical local PaperSessionConfig.

PostgreSQL owns operational materialization provenance and reconciliation state.

The existing paper-trading filesystem remains authoritative for canonical executable config bytes and the deterministic local session_id.

Neither authority replaces the other.

## Identity model

The model keeps materialization_id, authorization_id, profile binding, mandate binding, simulation_id, config_checksum and session_id as distinct identities.

Operational PostgreSQL UUIDs never equal, alias or predict session_id.

Phase 7-09 introduces no second runtime session identity.

At most one materialization aggregate may exist for one authorization_id.

Different historical authorizations may point to the same session_id when they produce the same canonical PaperSessionConfig.

## Materialization lifecycle

The initial lifecycle is PREPARED -> MATERIALIZED.

PREPARED is the durable materialization authorization and reconciliation point.

After PREPARED commits, the exact canonical PaperSessionConfig may be published through the existing PaperTradingRepository.create(config).

If publication is interrupted, a retry must resume the same PREPARED materialization instead of creating another aggregate.

If the exact config already exists, retry verifies it and finalizes MATERIALIZED.

If the derived session_id contains conflicting config bytes, materialization fails closed.

## Current-authority invariants

A new PREPARED record requires an AUTHORIZED capital authorization, an APPROVED exact profile, an APPROVED bound mandate and an ACTIVE bound simulation.

The authorization profile binding must exactly match the approved profile revision and specification checksum.

The authorization quote asset must match both the simulation currency and the selected instrument quote asset.

Authorized capital is copied exactly as Decimal into PaperSessionConfig.initial_capital without float conversion.

The approved frozen strategy snapshot is materialized directly; the latest mutable strategy-definition payload must never silently replace it.

## Post-preparation authority semantics

Once PREPARED commits, later authorization revocation or profile/mandate archival does not delete or rewrite the historical materialization evidence.

A PREPARED materialization may still reconcile and publish that exact frozen config after such later lifecycle changes.

This recovery rule does not grant runtime execution authority.

A future runner-control delivery must define and revalidate its own activation requirements before executing the materialized session.

## PostgreSQL persistence direction

Phase 7-09 requires a durable PostgreSQL materialization authority and therefore expects a new migration.

Gate 1 defines no SQL schema; Gate 2B will define tables, constraints, lifecycle defenses, actor references, timestamps, RLS and revoked Data API privileges.

Existing migrations remain unchanged and remote Supabase application remains a separately controlled operational step.

## Explicitly out of scope

Phase 7-09 does not execute run_once, start or control a paper runner, collector or worker.

It does not publish state.json, process candles, synchronize or scan RAW data, call Binance, execute trading logic, create orders or fills, or settle PnL.

It does not implement the ADT Official Portfolio, capital eras, trading-horizon labels or real-capital execution.

Materialization creates an auditable immutable local paper configuration, but the resulting session remains non-running.
