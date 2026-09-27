"""Static contract tests for the PAPER recommendation approval migration."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "supabase" / "migrations" / "20260927080000_paper_recommendation_approvals.sql"


def _migration() -> str:
    return MIGRATION.read_text()


def test_migration_creates_closed_approval_table() -> None:
    text = _migration()

    assert "create table public.paper_recommendation_approvals" in text
    assert "approval_id uuid primary key" in text
    assert "schema_version smallint not null" in text
    assert "state text not null" in text
    assert "record_version bigint not null" in text


def test_migration_persists_exact_ml_binding_and_audit_evidence() -> None:
    text = _migration()

    required = (
        "recommendation_identity text not null",
        "recommendation_checksum text not null",
        "candidate_identity text not null",
        "candidate_checksum text not null",
        "configuration_checksum text not null",
        "parameters_checksum text not null",
        "approval_checksum text not null",
        "approved_by uuid not null",
        "approved_at timestamptz not null",
        "create_idempotency_key text not null",
        "create_intent_fingerprint text not null",
        "revoked_by uuid",
        "revoked_at timestamptz",
    )

    for fragment in required:
        assert fragment in text


def test_migration_enforces_actor_scoped_idempotency() -> None:
    text = _migration()

    assert "paper_rec_approval_actor_idempotency_key" in text
    assert "unique (approved_by, create_idempotency_key)" in text


def test_migration_enforces_lifecycle_shape() -> None:
    text = _migration()

    assert "state in ('APPROVED', 'REVOKED')" in text
    assert "state = 'APPROVED'" in text
    assert "record_version = 1" in text
    assert "state = 'REVOKED'" in text
    assert "record_version >= 2" in text
    assert "revoked_at >= approved_at" in text


def test_migration_enforces_sha256_shapes() -> None:
    text = _migration()

    for column in (
        "recommendation_identity",
        "recommendation_checksum",
        "candidate_identity",
        "candidate_checksum",
        "configuration_checksum",
        "parameters_checksum",
        "approval_checksum",
        "create_intent_fingerprint",
    ):
        assert f"{column} ~ '^[0-9a-f]{{64}}$'" in text


def test_migration_protects_immutable_approval_binding() -> None:
    text = _migration()

    assert "create function public.validate_paper_recommendation_approval_update()" in text
    assert "paper_recommendation_approval_binding_is_immutable" in text
    assert "create trigger paper_rec_approval_validate_update" in text

    mutable = (
        "state",
        "record_version",
        "revoked_by",
        "revoked_at",
    )

    function_start = text.index(
        "create function public.validate_paper_recommendation_approval_update()"
    )
    trigger_start = text.index("create trigger paper_rec_approval_validate_update")
    body = text[function_start:trigger_start]

    for column in mutable:
        assert f"new.{column} is distinct from old.{column}" not in body


def test_migration_closes_data_api_surface() -> None:
    text = _migration()

    assert "alter table public.paper_recommendation_approvals enable row level security" in text
    assert "from anon, authenticated" in text


def test_migration_enforces_single_terminal_revocation_transition() -> None:
    migration = (
        Path(__file__).parents[3]
        / "supabase"
        / "migrations"
        / "20260927080000_paper_recommendation_approvals.sql"
    ).read_text()

    assert "old.state <> 'APPROVED'" in migration
    assert "new.state <> 'REVOKED'" in migration

    assert "old.record_version <> 1" in migration
    assert "new.record_version <> old.record_version + 1" in migration
    assert "new.record_version <> 2" in migration

    assert "paper_recommendation_approval_state_transition_forbidden" in migration
    assert "paper_recommendation_approval_record_version_conflict" in migration
    assert "paper_recommendation_approval_revocation_audit_invalid" in migration
