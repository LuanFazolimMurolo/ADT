"""Pure domain contracts for operational paper-capital authorizations."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Final
from uuid import UUID

from app.backtesting.serialization import canonical_json_bytes, decimal_text
from app.operational_paper_capital_authorizations.errors import (
    InvalidOperationalPaperCapitalAuthorizationSpecificationError,
    OperationalPaperCapitalAuthorizationBoundsExceededError,
    OperationalPaperCapitalAuthorizationChecksumMismatchError,
    OperationalPaperCapitalAuthorizationStateTransitionConflictError,
)

OPERATIONAL_PAPER_CAPITAL_AUTHORIZATION_SCHEMA_VERSION: Final = 1
OPERATIONAL_PAPER_CAPITAL_AUTHORIZATION_CREATE_CONTRACT_VERSION: Final = 1
MAX_OPERATIONAL_PAPER_CAPITAL_AUTHORIZATION_IDEMPOTENCY_KEY_LENGTH: Final = 128

OPERATIONAL_PAPER_CAPITAL_AUTHORIZATION_QUANTUM: Final = Decimal("0.00000001")
MAX_OPERATIONAL_PAPER_CAPITAL_AUTHORIZED_CAPITAL: Final = Decimal("999999999999.99999999")

_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ASSET = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_POSTGRESQL_BIGINT_MAX: Final = (1 << 63) - 1


class OperationalPaperCapitalAuthorizationState(StrEnum):
    AUTHORIZED = "AUTHORIZED"
    REVOKED = "REVOKED"


_ALLOWED_TRANSITIONS: Final = {
    OperationalPaperCapitalAuthorizationState.AUTHORIZED: frozenset(
        {OperationalPaperCapitalAuthorizationState.REVOKED}
    ),
    OperationalPaperCapitalAuthorizationState.REVOKED: frozenset(),
}


def is_operational_paper_capital_authorization_transition_allowed(
    current: OperationalPaperCapitalAuthorizationState,
    target: OperationalPaperCapitalAuthorizationState,
) -> bool:
    if not isinstance(current, OperationalPaperCapitalAuthorizationState):
        return False
    if not isinstance(target, OperationalPaperCapitalAuthorizationState):
        return False
    return target in _ALLOWED_TRANSITIONS[current]


def require_operational_paper_capital_authorization_transition(
    current: OperationalPaperCapitalAuthorizationState,
    target: OperationalPaperCapitalAuthorizationState,
) -> None:
    if not is_operational_paper_capital_authorization_transition_allowed(
        current,
        target,
    ):
        raise OperationalPaperCapitalAuthorizationStateTransitionConflictError()


@dataclass(frozen=True, slots=True)
class OperationalPaperCapitalAuthorizationProfileBinding:
    profile_id: UUID
    approved_revision: int
    specification_checksum: str

    def __post_init__(self) -> None:
        try:
            profile_id = _require_uuid(self.profile_id)
            revision = _require_positive_bigint(self.approved_revision)
            checksum = _require_sha256(self.specification_checksum)
        except OperationalPaperCapitalAuthorizationBoundsExceededError:
            raise
        except Exception:
            raise InvalidOperationalPaperCapitalAuthorizationSpecificationError() from None
        object.__setattr__(self, "profile_id", profile_id)
        object.__setattr__(self, "approved_revision", revision)
        object.__setattr__(self, "specification_checksum", checksum)


@dataclass(frozen=True, slots=True)
class OperationalPaperCapitalAuthorizationSpecification:
    schema_version: int
    profile_binding: OperationalPaperCapitalAuthorizationProfileBinding
    simulation_id: UUID
    quote_asset: str
    authorized_capital: Decimal

    def __post_init__(self) -> None:
        try:
            if (
                type(self.schema_version) is not int
                or self.schema_version != OPERATIONAL_PAPER_CAPITAL_AUTHORIZATION_SCHEMA_VERSION
            ):
                raise ValueError
            binding = _revalidate_profile_binding(self.profile_binding)
            simulation_id = _require_uuid(self.simulation_id)
            quote_asset = _canonical_asset(self.quote_asset)
            authorized_capital = _canonical_authorized_capital(self.authorized_capital)
        except OperationalPaperCapitalAuthorizationBoundsExceededError:
            raise
        except Exception:
            raise InvalidOperationalPaperCapitalAuthorizationSpecificationError() from None
        object.__setattr__(self, "profile_binding", binding)
        object.__setattr__(self, "simulation_id", simulation_id)
        object.__setattr__(self, "quote_asset", quote_asset)
        object.__setattr__(self, "authorized_capital", authorized_capital)


def operational_paper_capital_authorization_specification_payload(
    specification: OperationalPaperCapitalAuthorizationSpecification,
) -> dict[str, object]:
    canonical = _revalidate_specification(specification)
    return {
        "schema_version": canonical.schema_version,
        "profile_id": str(canonical.profile_binding.profile_id),
        "profile_approved_revision": canonical.profile_binding.approved_revision,
        "profile_specification_checksum": (canonical.profile_binding.specification_checksum),
        "simulation_id": str(canonical.simulation_id),
        "quote_asset": canonical.quote_asset,
        "authorized_capital": canonical.authorized_capital,
    }


def operational_paper_capital_authorization_specification_bytes(
    specification: OperationalPaperCapitalAuthorizationSpecification,
) -> bytes:
    return canonical_json_bytes(
        operational_paper_capital_authorization_specification_payload(specification)
    )


def operational_paper_capital_authorization_specification_checksum(
    specification: OperationalPaperCapitalAuthorizationSpecification,
) -> str:
    return hashlib.sha256(
        operational_paper_capital_authorization_specification_bytes(specification)
    ).hexdigest()


def validate_operational_paper_capital_authorization_specification_checksum(
    specification: OperationalPaperCapitalAuthorizationSpecification,
    expected_checksum: object,
) -> OperationalPaperCapitalAuthorizationSpecification:
    canonical = _revalidate_specification(specification)
    try:
        checksum = _require_sha256(expected_checksum)
    except Exception:
        raise InvalidOperationalPaperCapitalAuthorizationSpecificationError() from None
    if operational_paper_capital_authorization_specification_checksum(canonical) != checksum:
        raise OperationalPaperCapitalAuthorizationChecksumMismatchError()
    return canonical


def operational_paper_capital_authorization_specifications_equal(
    left: OperationalPaperCapitalAuthorizationSpecification,
    right: OperationalPaperCapitalAuthorizationSpecification,
) -> bool:
    return operational_paper_capital_authorization_specification_bytes(
        left
    ) == operational_paper_capital_authorization_specification_bytes(right)


@dataclass(frozen=True, slots=True)
class OperationalPaperCapitalAuthorizationCreateIntent:
    profile_binding: OperationalPaperCapitalAuthorizationProfileBinding
    simulation_id: UUID
    quote_asset: str
    authorized_capital: Decimal

    def __post_init__(self) -> None:
        try:
            binding = _revalidate_profile_binding(self.profile_binding)
            simulation_id = _require_uuid(self.simulation_id)
            quote_asset = _canonical_asset(self.quote_asset)
            authorized_capital = _canonical_authorized_capital(self.authorized_capital)
        except OperationalPaperCapitalAuthorizationBoundsExceededError:
            raise
        except Exception:
            raise InvalidOperationalPaperCapitalAuthorizationSpecificationError() from None
        object.__setattr__(self, "profile_binding", binding)
        object.__setattr__(self, "simulation_id", simulation_id)
        object.__setattr__(self, "quote_asset", quote_asset)
        object.__setattr__(self, "authorized_capital", authorized_capital)


def operational_paper_capital_authorization_create_intent_fingerprint(
    intent: OperationalPaperCapitalAuthorizationCreateIntent,
) -> str:
    canonical = _revalidate_create_intent(intent)
    payload = {
        "contract_version": (OPERATIONAL_PAPER_CAPITAL_AUTHORIZATION_CREATE_CONTRACT_VERSION),
        "profile_id": str(canonical.profile_binding.profile_id),
        "profile_approved_revision": canonical.profile_binding.approved_revision,
        "profile_specification_checksum": (canonical.profile_binding.specification_checksum),
        "simulation_id": str(canonical.simulation_id),
        "quote_asset": canonical.quote_asset,
        "authorized_capital": canonical.authorized_capital,
    }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def validate_operational_paper_capital_authorization_idempotency_key(
    value: object,
) -> str:
    if not isinstance(value, str):
        raise InvalidOperationalPaperCapitalAuthorizationSpecificationError()
    if not (1 <= len(value) <= MAX_OPERATIONAL_PAPER_CAPITAL_AUTHORIZATION_IDEMPOTENCY_KEY_LENGTH):
        raise OperationalPaperCapitalAuthorizationBoundsExceededError()
    if _SAFE_TOKEN.fullmatch(value) is None:
        raise InvalidOperationalPaperCapitalAuthorizationSpecificationError()
    return value


def build_operational_paper_capital_authorization_specification(
    intent: OperationalPaperCapitalAuthorizationCreateIntent,
) -> OperationalPaperCapitalAuthorizationSpecification:
    canonical = _revalidate_create_intent(intent)
    return OperationalPaperCapitalAuthorizationSpecification(
        schema_version=OPERATIONAL_PAPER_CAPITAL_AUTHORIZATION_SCHEMA_VERSION,
        profile_binding=canonical.profile_binding,
        simulation_id=canonical.simulation_id,
        quote_asset=canonical.quote_asset,
        authorized_capital=canonical.authorized_capital,
    )


@dataclass(frozen=True, slots=True)
class OperationalPaperCapitalAuthorization:
    authorization_id: UUID
    schema_version: int
    state: OperationalPaperCapitalAuthorizationState
    record_version: int
    profile_binding: OperationalPaperCapitalAuthorizationProfileBinding
    simulation_id: UUID
    quote_asset: str
    authorized_capital: Decimal
    authorization_checksum: str
    created_by: UUID
    created_at: datetime
    revoked_by: UUID | None
    revoked_at: datetime | None
    create_idempotency_key: str
    create_intent_fingerprint: str

    def __post_init__(self) -> None:
        try:
            authorization_id = _require_uuid(self.authorization_id)
            if (
                type(self.schema_version) is not int
                or self.schema_version != OPERATIONAL_PAPER_CAPITAL_AUTHORIZATION_SCHEMA_VERSION
            ):
                raise ValueError
            if not isinstance(
                self.state,
                OperationalPaperCapitalAuthorizationState,
            ):
                raise ValueError
            record_version = _require_positive_bigint(self.record_version)
            binding = _revalidate_profile_binding(self.profile_binding)
            simulation_id = _require_uuid(self.simulation_id)
            quote_asset = _canonical_asset(self.quote_asset)
            authorized_capital = _canonical_authorized_capital(self.authorized_capital)
            authorization_checksum = _require_sha256(self.authorization_checksum)
            specification = OperationalPaperCapitalAuthorizationSpecification(
                schema_version=self.schema_version,
                profile_binding=binding,
                simulation_id=simulation_id,
                quote_asset=quote_asset,
                authorized_capital=authorized_capital,
            )
            if (
                operational_paper_capital_authorization_specification_checksum(specification)
                != authorization_checksum
            ):
                raise OperationalPaperCapitalAuthorizationChecksumMismatchError()
            created_by = _require_uuid(self.created_by)
            created_at = _require_utc(self.created_at)
            key = validate_operational_paper_capital_authorization_idempotency_key(
                self.create_idempotency_key
            )
            fingerprint = _require_sha256(self.create_intent_fingerprint)
            revoked = _collective_presence(self.revoked_by, self.revoked_at)
            revoked_by: UUID | None = None
            revoked_at: datetime | None = None
            if revoked:
                if self.revoked_by is None or self.revoked_at is None:
                    raise ValueError
                revoked_by = _require_uuid(self.revoked_by)
                revoked_at = _require_utc(self.revoked_at)
                if revoked_at < created_at:
                    raise ValueError
            valid_state = (
                self.state is OperationalPaperCapitalAuthorizationState.AUTHORIZED and not revoked
            ) or (self.state is OperationalPaperCapitalAuthorizationState.REVOKED and revoked)
            if not valid_state:
                raise ValueError
        except OperationalPaperCapitalAuthorizationBoundsExceededError:
            raise
        except OperationalPaperCapitalAuthorizationChecksumMismatchError:
            raise
        except Exception:
            raise InvalidOperationalPaperCapitalAuthorizationSpecificationError() from None
        object.__setattr__(self, "authorization_id", authorization_id)
        object.__setattr__(self, "record_version", record_version)
        object.__setattr__(self, "profile_binding", binding)
        object.__setattr__(self, "simulation_id", simulation_id)
        object.__setattr__(self, "quote_asset", quote_asset)
        object.__setattr__(self, "authorized_capital", authorized_capital)
        object.__setattr__(
            self,
            "authorization_checksum",
            authorization_checksum,
        )
        object.__setattr__(self, "created_by", created_by)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "revoked_by", revoked_by)
        object.__setattr__(self, "revoked_at", revoked_at)
        object.__setattr__(self, "create_idempotency_key", key)
        object.__setattr__(self, "create_intent_fingerprint", fingerprint)


def _revalidate_profile_binding(
    value: object,
) -> OperationalPaperCapitalAuthorizationProfileBinding:
    if not isinstance(
        value,
        OperationalPaperCapitalAuthorizationProfileBinding,
    ):
        raise ValueError
    return OperationalPaperCapitalAuthorizationProfileBinding(
        profile_id=value.profile_id,
        approved_revision=value.approved_revision,
        specification_checksum=value.specification_checksum,
    )


def _revalidate_specification(
    value: object,
) -> OperationalPaperCapitalAuthorizationSpecification:
    if not isinstance(
        value,
        OperationalPaperCapitalAuthorizationSpecification,
    ):
        raise InvalidOperationalPaperCapitalAuthorizationSpecificationError()
    return OperationalPaperCapitalAuthorizationSpecification(
        schema_version=value.schema_version,
        profile_binding=value.profile_binding,
        simulation_id=value.simulation_id,
        quote_asset=value.quote_asset,
        authorized_capital=value.authorized_capital,
    )


def _revalidate_create_intent(
    value: object,
) -> OperationalPaperCapitalAuthorizationCreateIntent:
    if not isinstance(
        value,
        OperationalPaperCapitalAuthorizationCreateIntent,
    ):
        raise InvalidOperationalPaperCapitalAuthorizationSpecificationError()
    return OperationalPaperCapitalAuthorizationCreateIntent(
        profile_binding=value.profile_binding,
        simulation_id=value.simulation_id,
        quote_asset=value.quote_asset,
        authorized_capital=value.authorized_capital,
    )


def _require_uuid(value: object) -> UUID:
    if not isinstance(value, UUID) or value.int == 0:
        raise ValueError
    return value


def _require_positive_bigint(value: object) -> int:
    if type(value) is not int or value < 1:
        raise ValueError
    if value > _POSTGRESQL_BIGINT_MAX:
        raise OperationalPaperCapitalAuthorizationBoundsExceededError()
    return value


def _require_sha256(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError
    return value


def _require_utc(value: object) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ValueError
    return value.astimezone(UTC)


def _collective_presence(*values: object | None) -> bool:
    present = tuple(value is not None for value in values)
    if any(present) and not all(present):
        raise ValueError
    return all(present)


def _canonical_asset(value: object) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise ValueError
    canonical = value.strip().upper()
    if _ASSET.fullmatch(canonical) is None:
        raise ValueError
    return canonical


def _canonical_authorized_capital(value: object) -> Decimal:
    if type(value) is not Decimal:
        raise ValueError
    if (
        not value.is_finite()
        or value <= 0
        or value > MAX_OPERATIONAL_PAPER_CAPITAL_AUTHORIZED_CAPITAL
    ):
        raise ValueError
    try:
        persisted = value.quantize(OPERATIONAL_PAPER_CAPITAL_AUTHORIZATION_QUANTUM)
    except InvalidOperation:
        raise ValueError from None
    if persisted != value:
        raise ValueError
    return Decimal(decimal_text(value))
