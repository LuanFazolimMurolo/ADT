"""Human PAPER recommendation approval evidence.

Approval is an administrative decision only. It does not authorize capital,
materialize or activate a PAPER session, create a run epoch, execute an order,
or confer any LIVE authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class PaperRecommendationApprovalError(ValueError):
    """Base error for invalid PAPER recommendation approval evidence."""


class InvalidPaperRecommendationApprovalError(PaperRecommendationApprovalError):
    """Raised when an approval contract is malformed."""


class PaperRecommendationApprovalStateTransitionError(PaperRecommendationApprovalError):
    """Raised when an approval state transition is invalid."""


class PaperRecommendationApprovalState(StrEnum):
    APPROVED = "APPROVED"
    REVOKED = "REVOKED"


@dataclass(frozen=True, slots=True)
class PaperRecommendationApprovalIntent:
    """Exact immutable ML recommendation/candidate binding reviewed by a human."""

    recommendation_identity: str
    recommendation_checksum: str
    candidate_identity: str
    candidate_checksum: str
    configuration_checksum: str
    parameters_checksum: str

    def __post_init__(self) -> None:
        for value in (
            self.recommendation_identity,
            self.recommendation_checksum,
            self.candidate_identity,
            self.candidate_checksum,
            self.configuration_checksum,
            self.parameters_checksum,
        ):
            _require_sha256(value)


@dataclass(frozen=True, slots=True)
class PaperRecommendationApproval:
    """Recorded human approval evidence with no execution authority."""

    approval_id: UUID
    schema_version: int
    state: PaperRecommendationApprovalState
    record_version: int

    recommendation_identity: str
    recommendation_checksum: str
    candidate_identity: str
    candidate_checksum: str
    configuration_checksum: str
    parameters_checksum: str

    approval_checksum: str

    approved_by: UUID
    approved_at: datetime
    create_idempotency_key: str
    create_intent_fingerprint: str

    revoked_by: UUID | None
    revoked_at: datetime | None

    def __post_init__(self) -> None:
        if type(self.approval_id) is not UUID:
            raise InvalidPaperRecommendationApprovalError()

        if type(self.schema_version) is not int or self.schema_version != 1:
            raise InvalidPaperRecommendationApprovalError()

        if type(self.state) is not PaperRecommendationApprovalState:
            raise InvalidPaperRecommendationApprovalError()

        if type(self.record_version) is not int or self.record_version < 1:
            raise InvalidPaperRecommendationApprovalError()

        intent = PaperRecommendationApprovalIntent(
            recommendation_identity=self.recommendation_identity,
            recommendation_checksum=self.recommendation_checksum,
            candidate_identity=self.candidate_identity,
            candidate_checksum=self.candidate_checksum,
            configuration_checksum=self.configuration_checksum,
            parameters_checksum=self.parameters_checksum,
        )

        _require_sha256(self.approval_checksum)
        _require_sha256(self.create_intent_fingerprint)

        if type(self.approved_by) is not UUID:
            raise InvalidPaperRecommendationApprovalError()

        _require_utc(self.approved_at)
        _require_idempotency_key(self.create_idempotency_key)

        expected_fingerprint = paper_recommendation_approval_intent_fingerprint(intent)
        if self.create_intent_fingerprint != expected_fingerprint:
            raise InvalidPaperRecommendationApprovalError()

        expected_checksum = paper_recommendation_approval_checksum(
            approval_id=self.approval_id,
            intent=intent,
            approved_by=self.approved_by,
            approved_at=self.approved_at,
        )
        if self.approval_checksum != expected_checksum:
            raise InvalidPaperRecommendationApprovalError()

        if self.state is PaperRecommendationApprovalState.APPROVED:
            if self.record_version != 1:
                raise InvalidPaperRecommendationApprovalError()
            if self.revoked_by is not None or self.revoked_at is not None:
                raise InvalidPaperRecommendationApprovalError()
            return

        if self.state is PaperRecommendationApprovalState.REVOKED:
            if self.record_version < 2:
                raise InvalidPaperRecommendationApprovalError()
            if type(self.revoked_by) is not UUID or self.revoked_at is None:
                raise InvalidPaperRecommendationApprovalError()

            _require_utc(self.revoked_at)

            if self.revoked_at < self.approved_at:
                raise InvalidPaperRecommendationApprovalError()


def paper_recommendation_approval_intent_fingerprint(
    intent: PaperRecommendationApprovalIntent,
) -> str:
    if type(intent) is not PaperRecommendationApprovalIntent:
        raise InvalidPaperRecommendationApprovalError()

    return _sha256(
        {
            "candidate_checksum": intent.candidate_checksum,
            "candidate_identity": intent.candidate_identity,
            "configuration_checksum": intent.configuration_checksum,
            "parameters_checksum": intent.parameters_checksum,
            "recommendation_checksum": intent.recommendation_checksum,
            "recommendation_identity": intent.recommendation_identity,
        }
    )


def paper_recommendation_approval_checksum(
    *,
    approval_id: UUID,
    intent: PaperRecommendationApprovalIntent,
    approved_by: UUID,
    approved_at: datetime,
) -> str:
    if type(approval_id) is not UUID or type(approved_by) is not UUID:
        raise InvalidPaperRecommendationApprovalError()

    if type(intent) is not PaperRecommendationApprovalIntent:
        raise InvalidPaperRecommendationApprovalError()

    _require_utc(approved_at)

    return _sha256(
        {
            "approval_id": str(approval_id),
            "approved_at": approved_at.isoformat(),
            "approved_by": str(approved_by),
            "intent_fingerprint": paper_recommendation_approval_intent_fingerprint(intent),
            "schema_version": 1,
        }
    )


def approve_paper_recommendation(
    intent: PaperRecommendationApprovalIntent,
    *,
    approval_id: UUID,
    actor_id: UUID,
    approved_at: datetime,
    idempotency_key: str,
) -> PaperRecommendationApproval:
    if type(intent) is not PaperRecommendationApprovalIntent:
        raise InvalidPaperRecommendationApprovalError()

    if type(approval_id) is not UUID or type(actor_id) is not UUID:
        raise InvalidPaperRecommendationApprovalError()

    _require_utc(approved_at)
    _require_idempotency_key(idempotency_key)

    fingerprint = paper_recommendation_approval_intent_fingerprint(intent)

    return PaperRecommendationApproval(
        approval_id=approval_id,
        schema_version=1,
        state=PaperRecommendationApprovalState.APPROVED,
        record_version=1,
        recommendation_identity=intent.recommendation_identity,
        recommendation_checksum=intent.recommendation_checksum,
        candidate_identity=intent.candidate_identity,
        candidate_checksum=intent.candidate_checksum,
        configuration_checksum=intent.configuration_checksum,
        parameters_checksum=intent.parameters_checksum,
        approval_checksum=paper_recommendation_approval_checksum(
            approval_id=approval_id,
            intent=intent,
            approved_by=actor_id,
            approved_at=approved_at,
        ),
        approved_by=actor_id,
        approved_at=approved_at,
        create_idempotency_key=idempotency_key,
        create_intent_fingerprint=fingerprint,
        revoked_by=None,
        revoked_at=None,
    )


def revoke_paper_recommendation_approval(
    approval: PaperRecommendationApproval,
    *,
    actor_id: UUID,
    revoked_at: datetime,
) -> PaperRecommendationApproval:
    if type(approval) is not PaperRecommendationApproval:
        raise InvalidPaperRecommendationApprovalError()

    if approval.state is not PaperRecommendationApprovalState.APPROVED:
        raise PaperRecommendationApprovalStateTransitionError()

    if type(actor_id) is not UUID:
        raise InvalidPaperRecommendationApprovalError()

    _require_utc(revoked_at)

    if revoked_at < approval.approved_at:
        raise InvalidPaperRecommendationApprovalError()

    return replace(
        approval,
        state=PaperRecommendationApprovalState.REVOKED,
        record_version=approval.record_version + 1,
        revoked_by=actor_id,
        revoked_at=revoked_at,
    )


def _sha256(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_sha256(value: object) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise InvalidPaperRecommendationApprovalError()


def _require_utc(value: object) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise InvalidPaperRecommendationApprovalError()

    offset = value.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise InvalidPaperRecommendationApprovalError()


def _require_idempotency_key(value: object) -> None:
    if not isinstance(value, str) or _IDEMPOTENCY_KEY.fullmatch(value) is None:
        raise InvalidPaperRecommendationApprovalError()
