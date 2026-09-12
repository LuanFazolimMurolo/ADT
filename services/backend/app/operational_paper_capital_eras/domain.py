"""Pure domain contracts for official operational paper-capital eras."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Final
from uuid import UUID

from app.backtesting.serialization import canonical_json_bytes, decimal_text
from app.operational_paper_capital_eras.errors import (
    InvalidOperationalPaperCapitalEraSpecificationError,
    OperationalPaperCapitalEraBoundsExceededError,
    OperationalPaperCapitalEraChecksumMismatchError,
)

OPERATIONAL_PAPER_CAPITAL_ERA_SCHEMA_VERSION: Final = 1
OPERATIONAL_PAPER_CAPITAL_ERA_DESIGNATION_CONTRACT_VERSION: Final = 1
MAX_OPERATIONAL_PAPER_CAPITAL_ERA_IDEMPOTENCY_KEY_LENGTH: Final = 128

OPERATIONAL_PAPER_CAPITAL_ERA_QUANTUM: Final = Decimal("0.00000001")
MAX_OPERATIONAL_PAPER_CAPITAL_ERA_INITIAL_CAPITAL: Final = Decimal("999999999999.99999999")

_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CURRENCY = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

def _require_uuid(value: object) -> UUID:
    if not isinstance(value, UUID) or value.int == 0:
        raise ValueError
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


def validate_operational_paper_capital_era_idempotency_key(value: object) -> str:
    if not isinstance(value, str):
        raise InvalidOperationalPaperCapitalEraSpecificationError()
    if not (1 <= len(value) <= MAX_OPERATIONAL_PAPER_CAPITAL_ERA_IDEMPOTENCY_KEY_LENGTH):
        raise OperationalPaperCapitalEraBoundsExceededError()
    if _SAFE_TOKEN.fullmatch(value) is None:
        raise InvalidOperationalPaperCapitalEraSpecificationError()
    return value


def _canonical_currency(value: object) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise ValueError
    canonical = value.strip().upper()
    if _CURRENCY.fullmatch(canonical) is None:
        raise ValueError
    return canonical


def _canonical_initial_capital(value: object) -> Decimal:
    if type(value) is not Decimal:
        raise ValueError
    if (
        not value.is_finite()
        or value <= 0
        or value > MAX_OPERATIONAL_PAPER_CAPITAL_ERA_INITIAL_CAPITAL
    ):
        raise ValueError
    try:
        persisted = value.quantize(OPERATIONAL_PAPER_CAPITAL_ERA_QUANTUM)
    except InvalidOperation:
        raise ValueError from None
    if persisted != value:
        raise ValueError
    return Decimal(decimal_text(value))


@dataclass(frozen=True, slots=True)
class OperationalPaperCapitalEraSpecification:
    schema_version: int
    designation_contract_version: int
    simulation_id: UUID
    currency: str
    initial_capital: Decimal
    simulation_started_at: datetime

    def __post_init__(self) -> None:
        try:
            if (
                type(self.schema_version) is not int
                or self.schema_version != OPERATIONAL_PAPER_CAPITAL_ERA_SCHEMA_VERSION
            ):
                raise ValueError
            if (
                type(self.designation_contract_version) is not int
                or self.designation_contract_version
                != OPERATIONAL_PAPER_CAPITAL_ERA_DESIGNATION_CONTRACT_VERSION
            ):
                raise ValueError
            simulation_id = _require_uuid(self.simulation_id)
            currency = _canonical_currency(self.currency)
            initial_capital = _canonical_initial_capital(self.initial_capital)
            simulation_started_at = _require_utc(self.simulation_started_at)
        except OperationalPaperCapitalEraBoundsExceededError:
            raise
        except Exception:
            raise InvalidOperationalPaperCapitalEraSpecificationError() from None

        object.__setattr__(self, "simulation_id", simulation_id)
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "initial_capital", initial_capital)
        object.__setattr__(self, "simulation_started_at", simulation_started_at)


def _revalidate_specification(
    value: object,
) -> OperationalPaperCapitalEraSpecification:
    if not isinstance(value, OperationalPaperCapitalEraSpecification):
        raise InvalidOperationalPaperCapitalEraSpecificationError()
    return OperationalPaperCapitalEraSpecification(
        schema_version=value.schema_version,
        designation_contract_version=value.designation_contract_version,
        simulation_id=value.simulation_id,
        currency=value.currency,
        initial_capital=value.initial_capital,
        simulation_started_at=value.simulation_started_at,
    )


def operational_paper_capital_era_specification_payload(
    specification: OperationalPaperCapitalEraSpecification,
) -> dict[str, object]:
    canonical = _revalidate_specification(specification)
    return {
        "schema_version": canonical.schema_version,
        "designation_contract_version": canonical.designation_contract_version,
        "simulation_id": str(canonical.simulation_id),
        "currency": canonical.currency,
        "initial_capital": canonical.initial_capital,
        "simulation_started_at": canonical.simulation_started_at.isoformat(),
    }


def operational_paper_capital_era_specification_bytes(
    specification: OperationalPaperCapitalEraSpecification,
) -> bytes:
    return canonical_json_bytes(
        operational_paper_capital_era_specification_payload(specification)
    )


def operational_paper_capital_era_specification_checksum(
    specification: OperationalPaperCapitalEraSpecification,
) -> str:
    return hashlib.sha256(
        operational_paper_capital_era_specification_bytes(specification)
    ).hexdigest()


def validate_operational_paper_capital_era_specification_checksum(
    specification: OperationalPaperCapitalEraSpecification,
    expected_checksum: object,
) -> OperationalPaperCapitalEraSpecification:
    canonical = _revalidate_specification(specification)
    try:
        checksum = _require_sha256(expected_checksum)
    except Exception:
        raise InvalidOperationalPaperCapitalEraSpecificationError() from None
    if operational_paper_capital_era_specification_checksum(canonical) != checksum:
        raise OperationalPaperCapitalEraChecksumMismatchError()
    return canonical


def operational_paper_capital_era_specifications_equal(
    left: OperationalPaperCapitalEraSpecification,
    right: OperationalPaperCapitalEraSpecification,
) -> bool:
    return operational_paper_capital_era_specification_bytes(
        left
    ) == operational_paper_capital_era_specification_bytes(right)


@dataclass(frozen=True, slots=True)
class OperationalPaperCapitalEraDesignationIntent:
    simulation_id: UUID

    def __post_init__(self) -> None:
        try:
            simulation_id = _require_uuid(self.simulation_id)
        except Exception:
            raise InvalidOperationalPaperCapitalEraSpecificationError() from None
        object.__setattr__(self, "simulation_id", simulation_id)


def _revalidate_designation_intent(
    value: object,
) -> OperationalPaperCapitalEraDesignationIntent:
    if not isinstance(value, OperationalPaperCapitalEraDesignationIntent):
        raise InvalidOperationalPaperCapitalEraSpecificationError()
    return OperationalPaperCapitalEraDesignationIntent(
        simulation_id=value.simulation_id,
    )


def operational_paper_capital_era_designation_intent_fingerprint(
    intent: OperationalPaperCapitalEraDesignationIntent,
) -> str:
    canonical = _revalidate_designation_intent(intent)
    payload = {
        "contract_version": OPERATIONAL_PAPER_CAPITAL_ERA_DESIGNATION_CONTRACT_VERSION,
        "simulation_id": str(canonical.simulation_id),
    }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def build_operational_paper_capital_era_specification(
    intent: OperationalPaperCapitalEraDesignationIntent,
    *,
    currency: object,
    initial_capital: object,
    simulation_started_at: object,
) -> OperationalPaperCapitalEraSpecification:
    canonical_intent = _revalidate_designation_intent(intent)
    try:
        canonical_currency = _canonical_currency(currency)
        canonical_initial_capital = _canonical_initial_capital(initial_capital)
        canonical_started_at = _require_utc(simulation_started_at)
    except OperationalPaperCapitalEraBoundsExceededError:
        raise
    except Exception:
        raise InvalidOperationalPaperCapitalEraSpecificationError() from None

    return OperationalPaperCapitalEraSpecification(
        schema_version=OPERATIONAL_PAPER_CAPITAL_ERA_SCHEMA_VERSION,
        designation_contract_version=(
            OPERATIONAL_PAPER_CAPITAL_ERA_DESIGNATION_CONTRACT_VERSION
        ),
        simulation_id=canonical_intent.simulation_id,
        currency=canonical_currency,
        initial_capital=canonical_initial_capital,
        simulation_started_at=canonical_started_at,
    )


@dataclass(frozen=True, slots=True)
class OperationalPaperCapitalEra:
    era_id: UUID
    schema_version: int
    designation_contract_version: int
    simulation_id: UUID
    currency: str
    initial_capital: Decimal
    simulation_started_at: datetime
    era_checksum: str
    designated_by: UUID
    designated_at: datetime
    designation_idempotency_key: str
    designation_intent_fingerprint: str

    def __post_init__(self) -> None:
        try:
            era_id = _require_uuid(self.era_id)
            specification = OperationalPaperCapitalEraSpecification(
                schema_version=self.schema_version,
                designation_contract_version=self.designation_contract_version,
                simulation_id=self.simulation_id,
                currency=self.currency,
                initial_capital=self.initial_capital,
                simulation_started_at=self.simulation_started_at,
            )
            era_checksum = _require_sha256(self.era_checksum)
            if operational_paper_capital_era_specification_checksum(specification) != era_checksum:
                raise OperationalPaperCapitalEraChecksumMismatchError()
            designated_by = _require_uuid(self.designated_by)
            designated_at = _require_utc(self.designated_at)
            if designated_at < specification.simulation_started_at:
                raise ValueError
            key = validate_operational_paper_capital_era_idempotency_key(
                self.designation_idempotency_key
            )
            fingerprint = _require_sha256(self.designation_intent_fingerprint)
        except OperationalPaperCapitalEraBoundsExceededError:
            raise
        except OperationalPaperCapitalEraChecksumMismatchError:
            raise
        except Exception:
            raise InvalidOperationalPaperCapitalEraSpecificationError() from None

        object.__setattr__(self, "era_id", era_id)
        object.__setattr__(self, "simulation_id", specification.simulation_id)
        object.__setattr__(self, "currency", specification.currency)
        object.__setattr__(self, "initial_capital", specification.initial_capital)
        object.__setattr__(
            self,
            "simulation_started_at",
            specification.simulation_started_at,
        )
        object.__setattr__(self, "era_checksum", era_checksum)
        object.__setattr__(self, "designated_by", designated_by)
        object.__setattr__(self, "designated_at", designated_at)
        object.__setattr__(self, "designation_idempotency_key", key)
        object.__setattr__(
            self,
            "designation_intent_fingerprint",
            fingerprint,
        )


def _revalidate_era(value: object) -> OperationalPaperCapitalEra:
    if not isinstance(value, OperationalPaperCapitalEra):
        raise InvalidOperationalPaperCapitalEraSpecificationError()
    return OperationalPaperCapitalEra(
        era_id=value.era_id,
        schema_version=value.schema_version,
        designation_contract_version=value.designation_contract_version,
        simulation_id=value.simulation_id,
        currency=value.currency,
        initial_capital=value.initial_capital,
        simulation_started_at=value.simulation_started_at,
        era_checksum=value.era_checksum,
        designated_by=value.designated_by,
        designated_at=value.designated_at,
        designation_idempotency_key=value.designation_idempotency_key,
        designation_intent_fingerprint=value.designation_intent_fingerprint,
    )
