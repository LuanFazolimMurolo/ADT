"""Contract tests for human PAPER recommendation approval evidence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.paper_recommendation_approvals import (
    InvalidPaperRecommendationApprovalError,
    PaperRecommendationApproval,
    PaperRecommendationApprovalIntent,
    PaperRecommendationApprovalState,
    PaperRecommendationApprovalStateTransitionError,
    approve_paper_recommendation,
    paper_recommendation_approval_checksum,
    paper_recommendation_approval_intent_fingerprint,
    revoke_paper_recommendation_approval,
)

APPROVAL_ID = UUID("10000000-0000-4000-8000-000000000001")
ACTOR_ID = UUID("20000000-0000-4000-8000-000000000002")
OTHER_ACTOR_ID = UUID("30000000-0000-4000-8000-000000000003")
NOW = datetime(2026, 9, 27, 6, tzinfo=UTC)
KEY = "ml-paper-approval:test:1"


def _intent() -> PaperRecommendationApprovalIntent:
    return PaperRecommendationApprovalIntent(
        recommendation_identity="a" * 64,
        recommendation_checksum="b" * 64,
        candidate_identity="c" * 64,
        candidate_checksum="d" * 64,
        configuration_checksum="e" * 64,
        parameters_checksum="f" * 64,
    )


def _approval() -> PaperRecommendationApproval:
    return approve_paper_recommendation(
        _intent(),
        approval_id=APPROVAL_ID,
        actor_id=ACTOR_ID,
        approved_at=NOW,
        idempotency_key=KEY,
    )


def test_approval_freezes_exact_recommendation_and_candidate_binding() -> None:
    intent = _intent()

    approval = approve_paper_recommendation(
        intent,
        approval_id=APPROVAL_ID,
        actor_id=ACTOR_ID,
        approved_at=NOW,
        idempotency_key=KEY,
    )

    assert approval.state is PaperRecommendationApprovalState.APPROVED
    assert approval.record_version == 1

    assert approval.recommendation_identity == intent.recommendation_identity
    assert approval.recommendation_checksum == intent.recommendation_checksum
    assert approval.candidate_identity == intent.candidate_identity
    assert approval.candidate_checksum == intent.candidate_checksum
    assert approval.configuration_checksum == intent.configuration_checksum
    assert approval.parameters_checksum == intent.parameters_checksum

    assert approval.approved_by == ACTOR_ID
    assert approval.approved_at == NOW
    assert approval.create_idempotency_key == KEY
    assert approval.revoked_by is None
    assert approval.revoked_at is None


def test_intent_fingerprint_is_deterministic_and_binding_sensitive() -> None:
    intent = _intent()

    first = paper_recommendation_approval_intent_fingerprint(intent)
    second = paper_recommendation_approval_intent_fingerprint(intent)

    assert first == second
    assert len(first) == 64

    changed = PaperRecommendationApprovalIntent(
        recommendation_identity=intent.recommendation_identity,
        recommendation_checksum=intent.recommendation_checksum,
        candidate_identity=intent.candidate_identity,
        candidate_checksum="0" * 64,
        configuration_checksum=intent.configuration_checksum,
        parameters_checksum=intent.parameters_checksum,
    )

    assert paper_recommendation_approval_intent_fingerprint(changed) != first


def test_approval_checksum_is_deterministic() -> None:
    intent = _intent()

    first = paper_recommendation_approval_checksum(
        approval_id=APPROVAL_ID,
        intent=intent,
        approved_by=ACTOR_ID,
        approved_at=NOW,
    )
    second = paper_recommendation_approval_checksum(
        approval_id=APPROVAL_ID,
        intent=intent,
        approved_by=ACTOR_ID,
        approved_at=NOW,
    )

    assert first == second
    assert len(first) == 64


def test_revocation_changes_only_lifecycle_evidence() -> None:
    approved = _approval()

    revoked = revoke_paper_recommendation_approval(
        approved,
        actor_id=OTHER_ACTOR_ID,
        revoked_at=NOW + timedelta(minutes=1),
    )

    assert revoked.state is PaperRecommendationApprovalState.REVOKED
    assert revoked.record_version == 2
    assert revoked.revoked_by == OTHER_ACTOR_ID
    assert revoked.revoked_at == NOW + timedelta(minutes=1)

    assert revoked.approval_id == approved.approval_id
    assert revoked.recommendation_identity == approved.recommendation_identity
    assert revoked.recommendation_checksum == approved.recommendation_checksum
    assert revoked.candidate_identity == approved.candidate_identity
    assert revoked.candidate_checksum == approved.candidate_checksum
    assert revoked.configuration_checksum == approved.configuration_checksum
    assert revoked.parameters_checksum == approved.parameters_checksum
    assert revoked.approval_checksum == approved.approval_checksum
    assert revoked.create_idempotency_key == approved.create_idempotency_key
    assert revoked.create_intent_fingerprint == approved.create_intent_fingerprint


def test_revoked_approval_cannot_be_revoked_again() -> None:
    revoked = revoke_paper_recommendation_approval(
        _approval(),
        actor_id=OTHER_ACTOR_ID,
        revoked_at=NOW + timedelta(minutes=1),
    )

    with pytest.raises(PaperRecommendationApprovalStateTransitionError):
        revoke_paper_recommendation_approval(
            revoked,
            actor_id=ACTOR_ID,
            revoked_at=NOW + timedelta(minutes=2),
        )


def test_approval_rejects_non_utc_timestamps() -> None:
    naive = datetime(2026, 9, 27, 6)

    with pytest.raises(InvalidPaperRecommendationApprovalError):
        approve_paper_recommendation(
            _intent(),
            approval_id=APPROVAL_ID,
            actor_id=ACTOR_ID,
            approved_at=naive,
            idempotency_key=KEY,
        )


def test_approval_rejects_invalid_hash_binding() -> None:
    with pytest.raises(InvalidPaperRecommendationApprovalError):
        PaperRecommendationApprovalIntent(
            recommendation_identity="not-a-hash",
            recommendation_checksum="b" * 64,
            candidate_identity="c" * 64,
            candidate_checksum="d" * 64,
            configuration_checksum="e" * 64,
            parameters_checksum="f" * 64,
        )


@pytest.mark.parametrize(
    "key",
    (
        "",
        " contains-space",
        "contains space",
        "x" * 129,
    ),
)
def test_approval_rejects_invalid_idempotency_keys(key: str) -> None:
    with pytest.raises(InvalidPaperRecommendationApprovalError):
        approve_paper_recommendation(
            _intent(),
            approval_id=APPROVAL_ID,
            actor_id=ACTOR_ID,
            approved_at=NOW,
            idempotency_key=key,
        )


def test_reconstructed_approval_rejects_tampered_fingerprint() -> None:
    approval = _approval()

    with pytest.raises(InvalidPaperRecommendationApprovalError):
        PaperRecommendationApproval(
            approval_id=approval.approval_id,
            schema_version=approval.schema_version,
            state=approval.state,
            record_version=approval.record_version,
            recommendation_identity=approval.recommendation_identity,
            recommendation_checksum=approval.recommendation_checksum,
            candidate_identity=approval.candidate_identity,
            candidate_checksum=approval.candidate_checksum,
            configuration_checksum=approval.configuration_checksum,
            parameters_checksum=approval.parameters_checksum,
            approval_checksum=approval.approval_checksum,
            approved_by=approval.approved_by,
            approved_at=approval.approved_at,
            create_idempotency_key=approval.create_idempotency_key,
            create_intent_fingerprint="0" * 64,
            revoked_by=None,
            revoked_at=None,
        )


def test_reconstructed_approval_rejects_tampered_checksum() -> None:
    approval = _approval()

    with pytest.raises(InvalidPaperRecommendationApprovalError):
        PaperRecommendationApproval(
            approval_id=approval.approval_id,
            schema_version=approval.schema_version,
            state=approval.state,
            record_version=approval.record_version,
            recommendation_identity=approval.recommendation_identity,
            recommendation_checksum=approval.recommendation_checksum,
            candidate_identity=approval.candidate_identity,
            candidate_checksum=approval.candidate_checksum,
            configuration_checksum=approval.configuration_checksum,
            parameters_checksum=approval.parameters_checksum,
            approval_checksum="0" * 64,
            approved_by=approval.approved_by,
            approved_at=approval.approved_at,
            create_idempotency_key=approval.create_idempotency_key,
            create_intent_fingerprint=approval.create_intent_fingerprint,
            revoked_by=None,
            revoked_at=None,
        )
