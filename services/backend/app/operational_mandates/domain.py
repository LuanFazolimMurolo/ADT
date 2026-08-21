"""Pure operational-mandate domain contracts."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final
from uuid import UUID

from app.domain.errors import DomainError
from app.market_data.domain import Exchange, MarketType, TradingPair
from app.operational_mandates.errors import (
    InvalidOperationalMandateSpecificationError,
    OperationalMandateBoundsExceededError,
    OperationalMandateChecksumMismatchError,
    OperationalMandateStateTransitionConflictError,
    UnsupportedOperationalMandateCapabilityError,
)

OPERATIONAL_MANDATE_SPEC_SCHEMA_VERSION: Final = 1
OPERATIONAL_MANDATE_CREATE_CONTRACT_VERSION: Final = 1

MAX_OPERATIONAL_MANDATE_NAME_LENGTH: Final = 120
MAX_OPERATIONAL_MANDATE_DESCRIPTION_LENGTH: Final = 1_000
MAX_OPERATIONAL_MANDATE_INSTRUMENTS: Final = 100
MAX_OPERATIONAL_MANDATE_IDEMPOTENCY_KEY_LENGTH: Final = 128

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

SUPPORTED_OPERATIONAL_MANDATE_CAPABILITIES: Final[frozenset[tuple[Exchange, MarketType]]] = (
    frozenset({(Exchange.BINANCE, MarketType.SPOT)})
)


@dataclass(frozen=True, slots=True)
class OperationalMandateInstrument:
    """Canonical instrument identity authorized by one mandate revision."""

    exchange: Exchange
    market_type: MarketType
    pair: TradingPair

    def __post_init__(self) -> None:
        if not isinstance(self.exchange, Exchange) or not isinstance(self.market_type, MarketType):
            raise InvalidOperationalMandateSpecificationError()
        canonical_pair = _revalidate_pair(self.pair)
        object.__setattr__(self, "pair", canonical_pair)

    @property
    def canonical_key(self) -> tuple[str, str, str, str]:
        """Return the deterministic authorization identity and ordering key."""

        return (
            self.exchange.value,
            self.market_type.value,
            self.pair.base,
            self.pair.quote,
        )


def validate_operational_mandate_instrument(
    value: object,
) -> OperationalMandateInstrument:
    """Rebuild one canonical instrument without trusting frozen-object integrity."""

    if not isinstance(value, OperationalMandateInstrument):
        raise InvalidOperationalMandateSpecificationError()
    return OperationalMandateInstrument(
        exchange=value.exchange,
        market_type=value.market_type,
        pair=value.pair,
    )


def require_operational_mandate_capability(
    instrument: OperationalMandateInstrument,
) -> OperationalMandateInstrument:
    """Require an explicitly supported exchange and market combination."""

    canonical = validate_operational_mandate_instrument(instrument)
    if (canonical.exchange, canonical.market_type) not in (
        SUPPORTED_OPERATIONAL_MANDATE_CAPABILITIES
    ):
        raise UnsupportedOperationalMandateCapabilityError()
    return canonical


@dataclass(frozen=True, slots=True)
class OperationalMandateSpecification:
    """Normalized immutable specification for one mandate revision."""

    schema_version: int
    name: str
    description: str
    instruments: tuple[OperationalMandateInstrument, ...]

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version != OPERATIONAL_MANDATE_SPEC_SCHEMA_VERSION
        ):
            raise InvalidOperationalMandateSpecificationError()
        name = _normalize_human_text(
            self.name,
            maximum=MAX_OPERATIONAL_MANDATE_NAME_LENGTH,
            allow_empty=False,
        )
        description = _normalize_human_text(
            self.description,
            maximum=MAX_OPERATIONAL_MANDATE_DESCRIPTION_LENGTH,
            allow_empty=True,
        )
        if not isinstance(self.instruments, tuple):
            raise InvalidOperationalMandateSpecificationError()
        if not 1 <= len(self.instruments) <= MAX_OPERATIONAL_MANDATE_INSTRUMENTS:
            raise OperationalMandateBoundsExceededError()

        instruments_by_key: dict[tuple[str, str, str, str], OperationalMandateInstrument] = {}
        for value in self.instruments:
            instrument = require_operational_mandate_capability(value)
            instruments_by_key[instrument.canonical_key] = instrument
        instruments = tuple(instruments_by_key[key] for key in sorted(instruments_by_key))
        if not 1 <= len(instruments) <= MAX_OPERATIONAL_MANDATE_INSTRUMENTS:
            raise OperationalMandateBoundsExceededError()

        object.__setattr__(self, "name", name)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "instruments", instruments)


def operational_mandate_specification_payload(
    specification: OperationalMandateSpecification,
) -> dict[str, object]:
    """Return the exact canonical JSON-compatible specification payload."""

    canonical = _revalidate_specification(specification)
    instruments = [
        {
            "exchange": instrument.exchange.value,
            "market_type": instrument.market_type.value,
            "base": instrument.pair.base,
            "quote": instrument.pair.quote,
        }
        for instrument in canonical.instruments
    ]
    return {
        "schema_version": canonical.schema_version,
        "name": canonical.name,
        "description": canonical.description,
        "instruments": instruments,
    }


def operational_mandate_specification_bytes(
    specification: OperationalMandateSpecification,
) -> bytes:
    """Encode canonical specification semantics as compact ASCII JSON."""

    return _canonical_json_bytes(operational_mandate_specification_payload(specification))


def operational_mandate_specification_checksum(
    specification: OperationalMandateSpecification,
) -> str:
    """Hash canonical specification semantics with SHA-256."""

    return hashlib.sha256(operational_mandate_specification_bytes(specification)).hexdigest()


def operational_mandate_specifications_equal(
    left: OperationalMandateSpecification,
    right: OperationalMandateSpecification,
) -> bool:
    """Compare normalized semantics without relying only on checksum equality."""

    return operational_mandate_specification_bytes(left) == operational_mandate_specification_bytes(
        right
    )


def validate_operational_mandate_idempotency_key(value: object) -> str:
    """Validate and return one exact administrator-supplied key."""

    if not isinstance(value, str):
        raise InvalidOperationalMandateSpecificationError()
    if not 1 <= len(value) <= MAX_OPERATIONAL_MANDATE_IDEMPOTENCY_KEY_LENGTH:
        raise OperationalMandateBoundsExceededError()
    if _IDEMPOTENCY_KEY_PATTERN.fullmatch(value) is None:
        raise InvalidOperationalMandateSpecificationError()
    return value


def operational_mandate_create_request_fingerprint(
    specification: OperationalMandateSpecification,
) -> str:
    """Hash the versioned canonical create-request contract."""

    payload = {
        "contract_version": OPERATIONAL_MANDATE_CREATE_CONTRACT_VERSION,
        "specification": operational_mandate_specification_payload(specification),
    }
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


class OperationalMandateState(StrEnum):
    """One-way lifecycle states for an operational mandate."""

    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    ARCHIVED = "ARCHIVED"


_ALLOWED_TRANSITIONS: Final[dict[OperationalMandateState, frozenset[OperationalMandateState]]] = {
    OperationalMandateState.DRAFT: frozenset(
        {OperationalMandateState.APPROVED, OperationalMandateState.ARCHIVED}
    ),
    OperationalMandateState.APPROVED: frozenset({OperationalMandateState.ARCHIVED}),
    OperationalMandateState.ARCHIVED: frozenset(),
}


def is_operational_mandate_transition_allowed(
    current: OperationalMandateState,
    target: OperationalMandateState,
) -> bool:
    """Return whether one normal lifecycle transition is allowed."""

    if not isinstance(current, OperationalMandateState) or not isinstance(
        target, OperationalMandateState
    ):
        return False
    return target in _ALLOWED_TRANSITIONS[current]


def require_operational_mandate_transition(
    current: OperationalMandateState,
    target: OperationalMandateState,
) -> None:
    """Reject same-state, reverse, and terminal lifecycle transitions."""

    if not is_operational_mandate_transition_allowed(current, target):
        raise OperationalMandateStateTransitionConflictError()


@dataclass(frozen=True, slots=True)
class OperationalMandateRevision:
    """Immutable validated snapshot of one specification revision."""

    mandate_id: UUID
    revision: int
    specification: OperationalMandateSpecification
    specification_checksum: str
    created_by: UUID
    created_at: datetime

    def __post_init__(self) -> None:
        _require_uuid(self.mandate_id)
        _require_positive_int(self.revision)
        specification = _revalidate_specification(self.specification)
        checksum = _require_sha256(self.specification_checksum)
        if operational_mandate_specification_checksum(specification) != checksum:
            raise OperationalMandateChecksumMismatchError()
        _require_uuid(self.created_by)
        created_at = _require_utc(self.created_at)
        object.__setattr__(self, "specification", specification)
        object.__setattr__(self, "created_at", created_at)


@dataclass(frozen=True, slots=True)
class OperationalMandate:
    """Immutable validated snapshot of one mandate aggregate."""

    mandate_id: UUID
    state: OperationalMandateState
    current_revision: int
    record_version: int
    approved_revision: int | None
    approved_checksum: str | None
    created_by: UUID
    created_at: datetime
    approved_by: UUID | None
    approved_at: datetime | None
    archived_by: UUID | None
    archived_at: datetime | None
    create_idempotency_key: str
    create_request_fingerprint: str

    def __post_init__(self) -> None:
        _require_uuid(self.mandate_id)
        if not isinstance(self.state, OperationalMandateState):
            raise InvalidOperationalMandateSpecificationError()
        _require_positive_int(self.current_revision)
        _require_positive_int(self.record_version)
        _require_uuid(self.created_by)
        created_at = _require_utc(self.created_at)
        idempotency_key = validate_operational_mandate_idempotency_key(self.create_idempotency_key)
        fingerprint = _require_sha256(self.create_request_fingerprint)

        approval_present = _collective_presence(
            self.approved_revision,
            self.approved_checksum,
            self.approved_by,
            self.approved_at,
        )
        archive_present = _collective_presence(self.archived_by, self.archived_at)

        approved_at: datetime | None = None
        if approval_present:
            approved_revision = self.approved_revision
            approved_checksum = self.approved_checksum
            approved_by = self.approved_by
            raw_approved_at = self.approved_at
            if (
                approved_revision is None
                or approved_checksum is None
                or approved_by is None
                or raw_approved_at is None
            ):
                raise InvalidOperationalMandateSpecificationError()
            _require_positive_int(approved_revision)
            _require_sha256(approved_checksum)
            _require_uuid(approved_by)
            approved_at = _require_utc(raw_approved_at)
            if approved_revision != self.current_revision or approved_at < created_at:
                raise InvalidOperationalMandateSpecificationError()

        archived_at: datetime | None = None
        if archive_present:
            archived_by = self.archived_by
            raw_archived_at = self.archived_at
            if archived_by is None or raw_archived_at is None:
                raise InvalidOperationalMandateSpecificationError()
            _require_uuid(archived_by)
            archived_at = _require_utc(raw_archived_at)
            if archived_at < created_at:
                raise InvalidOperationalMandateSpecificationError()

        if self.state is OperationalMandateState.DRAFT:
            if approval_present or archive_present:
                raise InvalidOperationalMandateSpecificationError()
        elif self.state is OperationalMandateState.APPROVED:
            if not approval_present or archive_present:
                raise InvalidOperationalMandateSpecificationError()
        elif not archive_present:
            raise InvalidOperationalMandateSpecificationError()

        if approved_at is not None and archived_at is not None and archived_at < approved_at:
            raise InvalidOperationalMandateSpecificationError()

        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "approved_at", approved_at)
        object.__setattr__(self, "archived_at", archived_at)
        object.__setattr__(self, "create_idempotency_key", idempotency_key)
        object.__setattr__(self, "create_request_fingerprint", fingerprint)


def _normalize_human_text(value: object, *, maximum: int, allow_empty: bool) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise InvalidOperationalMandateSpecificationError()
    normalized = unicodedata.normalize("NFC", value)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n").strip()
    if (not allow_empty and not normalized) or len(normalized) > maximum:
        raise OperationalMandateBoundsExceededError()
    return normalized


def _revalidate_pair(value: object) -> TradingPair:
    if (
        not isinstance(value, TradingPair)
        or not isinstance(value.base, str)
        or not isinstance(value.quote, str)
    ):
        raise InvalidOperationalMandateSpecificationError()
    try:
        canonical = TradingPair(value.base, value.quote)
    except DomainError:
        raise InvalidOperationalMandateSpecificationError() from None
    if canonical != value:
        raise InvalidOperationalMandateSpecificationError()
    return canonical


def _revalidate_specification(value: object) -> OperationalMandateSpecification:
    if not isinstance(value, OperationalMandateSpecification):
        raise InvalidOperationalMandateSpecificationError()
    return OperationalMandateSpecification(
        schema_version=value.schema_version,
        name=value.name,
        description=value.description,
        instruments=value.instruments,
    )


def _canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")


def _require_uuid(value: object) -> UUID:
    if not isinstance(value, UUID):
        raise InvalidOperationalMandateSpecificationError()
    return value


def _require_positive_int(value: object) -> int:
    if type(value) is not int or value < 1:
        raise InvalidOperationalMandateSpecificationError()
    return value


def _require_sha256(value: object) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise InvalidOperationalMandateSpecificationError()
    return value


def _require_utc(value: object) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset() != timedelta(0)
    ):
        raise InvalidOperationalMandateSpecificationError()
    return value.astimezone(UTC)


def _collective_presence(*values: object | None) -> bool:
    present = tuple(value is not None for value in values)
    if any(present) and not all(present):
        raise InvalidOperationalMandateSpecificationError()
    return all(present)
