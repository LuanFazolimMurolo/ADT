"""Pure operational market-data collector control domain contracts."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final
from uuid import UUID

from app.backtesting.serialization import canonical_json_bytes
from app.market_data.domain import TradingPair
from app.market_data.timeframes import get_timeframe
from app.operational_market_data_collectors.errors import (
    InvalidOperationalMarketDataCollectorSpecificationError,
    OperationalMarketDataCollectorBoundsExceededError,
    OperationalMarketDataCollectorChecksumMismatchError,
    OperationalMarketDataCollectorCommandConflictError,
    OperationalMarketDataCollectorLeaseError,
    OperationalMarketDataCollectorStateTransitionConflictError,
)

OPERATIONAL_MARKET_DATA_COLLECTOR_SCHEMA_VERSION: Final = 1
OPERATIONAL_MARKET_DATA_COLLECTOR_CONTRACT_VERSION: Final = 1
OPERATIONAL_MARKET_DATA_COLLECTOR_START_CONTRACT_VERSION: Final = 1
OPERATIONAL_MARKET_DATA_COLLECTOR_COMMAND_CONTRACT_VERSION: Final = 1

MAX_OPERATIONAL_MARKET_DATA_COLLECTOR_IDEMPOTENCY_KEY_LENGTH: Final = 128
MAX_OPERATIONAL_MARKET_DATA_COLLECTOR_TARGETS: Final = 1_000
MAX_OPERATIONAL_MARKET_DATA_COLLECTOR_BOOTSTRAP_CANDLES: Final = 1_000_000
MAX_OPERATIONAL_MARKET_DATA_COLLECTOR_INTERVAL_SECONDS: Final = 3_600
MAX_OPERATIONAL_MARKET_DATA_COLLECTOR_OVERLAP_CANDLES: Final = 100

_POSTGRESQL_BIGINT_MAX: Final = (1 << 63) - 1
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class OperationalMarketDataCollectorScope(StrEnum):
    BINANCE_SPOT_RAW = "BINANCE_SPOT_RAW"


class OperationalMarketDataCollectorDesiredState(StrEnum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"


class OperationalMarketDataCollectorObservedState(StrEnum):
    PENDING = "PENDING"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    RECOVERING = "RECOVERING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class OperationalMarketDataCollectorCommandType(StrEnum):
    START = "START"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    STOP = "STOP"


class OperationalMarketDataCollectorFailureCode(StrEnum):
    COLLECTOR_SPEC_INVALID = "COLLECTOR_SPEC_INVALID"
    LOCAL_COLLECTOR_BUSY = "LOCAL_COLLECTOR_BUSY"
    LOCAL_STATE_INVALID = "LOCAL_STATE_INVALID"
    LEASE_LOST = "LEASE_LOST"
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


TERMINAL_OPERATIONAL_MARKET_DATA_COLLECTOR_STATES: Final = frozenset(
    {
        OperationalMarketDataCollectorObservedState.STOPPED,
        OperationalMarketDataCollectorObservedState.FAILED,
    }
)

_ALLOWED_OBSERVED_TRANSITIONS: Final = {
    OperationalMarketDataCollectorObservedState.PENDING: frozenset(
        {
            OperationalMarketDataCollectorObservedState.STARTING,
            OperationalMarketDataCollectorObservedState.PAUSED,
            OperationalMarketDataCollectorObservedState.STOPPED,
            OperationalMarketDataCollectorObservedState.FAILED,
        }
    ),
    OperationalMarketDataCollectorObservedState.STARTING: frozenset(
        {
            OperationalMarketDataCollectorObservedState.RUNNING,
            OperationalMarketDataCollectorObservedState.PAUSED,
            OperationalMarketDataCollectorObservedState.RECOVERING,
            OperationalMarketDataCollectorObservedState.STOPPING,
            OperationalMarketDataCollectorObservedState.FAILED,
        }
    ),
    OperationalMarketDataCollectorObservedState.RUNNING: frozenset(
        {
            OperationalMarketDataCollectorObservedState.PAUSED,
            OperationalMarketDataCollectorObservedState.RECOVERING,
            OperationalMarketDataCollectorObservedState.STOPPING,
            OperationalMarketDataCollectorObservedState.FAILED,
        }
    ),
    OperationalMarketDataCollectorObservedState.PAUSED: frozenset(
        {
            OperationalMarketDataCollectorObservedState.STARTING,
            OperationalMarketDataCollectorObservedState.STOPPED,
            OperationalMarketDataCollectorObservedState.FAILED,
        }
    ),
    OperationalMarketDataCollectorObservedState.RECOVERING: frozenset(
        {
            OperationalMarketDataCollectorObservedState.STARTING,
            OperationalMarketDataCollectorObservedState.PAUSED,
            OperationalMarketDataCollectorObservedState.STOPPING,
            OperationalMarketDataCollectorObservedState.STOPPED,
            OperationalMarketDataCollectorObservedState.FAILED,
        }
    ),
    OperationalMarketDataCollectorObservedState.STOPPING: frozenset(
        {
            OperationalMarketDataCollectorObservedState.RECOVERING,
            OperationalMarketDataCollectorObservedState.STOPPED,
            OperationalMarketDataCollectorObservedState.FAILED,
        }
    ),
    OperationalMarketDataCollectorObservedState.STOPPED: frozenset(),
    OperationalMarketDataCollectorObservedState.FAILED: frozenset(),
}

_ALLOWED_DESIRED_COMMANDS: Final = {
    OperationalMarketDataCollectorDesiredState.RUNNING: frozenset(
        {
            OperationalMarketDataCollectorCommandType.PAUSE,
            OperationalMarketDataCollectorCommandType.STOP,
        }
    ),
    OperationalMarketDataCollectorDesiredState.PAUSED: frozenset(
        {
            OperationalMarketDataCollectorCommandType.RESUME,
            OperationalMarketDataCollectorCommandType.STOP,
        }
    ),
    OperationalMarketDataCollectorDesiredState.STOPPED: frozenset(),
}


def _require_uuid(value: object) -> UUID:
    if not isinstance(value, UUID) or value.int == 0:
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


def _require_sha256(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError
    return value


def _require_positive_bigint(value: object) -> int:
    if type(value) is not int or value < 1:
        raise ValueError
    if value > _POSTGRESQL_BIGINT_MAX:
        raise OperationalMarketDataCollectorBoundsExceededError()
    return value


def _require_nonnegative_bigint(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError
    if value > _POSTGRESQL_BIGINT_MAX:
        raise OperationalMarketDataCollectorBoundsExceededError()
    return value


def _next_record_version(value: int) -> int:
    current = _require_positive_bigint(value)
    if current >= _POSTGRESQL_BIGINT_MAX:
        raise OperationalMarketDataCollectorBoundsExceededError()
    return current + 1


def _next_fencing_token(value: int) -> int:
    current = _require_nonnegative_bigint(value)
    if current >= _POSTGRESQL_BIGINT_MAX:
        raise OperationalMarketDataCollectorBoundsExceededError()
    return current + 1


def validate_operational_market_data_collector_idempotency_key(
    value: object,
) -> str:
    if not isinstance(value, str):
        raise InvalidOperationalMarketDataCollectorSpecificationError()

    if not (1 <= len(value) <= MAX_OPERATIONAL_MARKET_DATA_COLLECTOR_IDEMPOTENCY_KEY_LENGTH):
        raise OperationalMarketDataCollectorBoundsExceededError()

    if _SAFE_TOKEN.fullmatch(value) is None:
        raise InvalidOperationalMarketDataCollectorSpecificationError()

    return value


@dataclass(frozen=True, slots=True)
class OperationalMarketDataCollectorTarget:
    symbol: str
    timeframe: str
    bootstrap_candles: int

    def __post_init__(self) -> None:
        try:
            if not isinstance(self.symbol, str):
                raise ValueError

            pair = TradingPair.parse(self.symbol)
            symbol = pair.symbol

            if not isinstance(self.timeframe, str):
                raise ValueError

            timeframe = get_timeframe(self.timeframe).code

            if type(self.bootstrap_candles) is not int:
                raise ValueError

            if (
                not 1
                <= self.bootstrap_candles
                <= (MAX_OPERATIONAL_MARKET_DATA_COLLECTOR_BOOTSTRAP_CANDLES)
            ):
                raise OperationalMarketDataCollectorBoundsExceededError()

        except OperationalMarketDataCollectorBoundsExceededError:
            raise
        except Exception:
            raise InvalidOperationalMarketDataCollectorSpecificationError() from None

        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "timeframe", timeframe)

    @property
    def key(self) -> str:
        return f"{self.symbol}:{self.timeframe}"


def _revalidate_target(
    value: object,
) -> OperationalMarketDataCollectorTarget:
    if not isinstance(value, OperationalMarketDataCollectorTarget):
        raise InvalidOperationalMarketDataCollectorSpecificationError()

    return OperationalMarketDataCollectorTarget(
        symbol=value.symbol,
        timeframe=value.timeframe,
        bootstrap_candles=value.bootstrap_candles,
    )


@dataclass(frozen=True, slots=True)
class OperationalMarketDataCollectorSpecification:
    schema_version: int
    collector_contract_version: int
    scope: OperationalMarketDataCollectorScope
    targets: tuple[OperationalMarketDataCollectorTarget, ...]
    interval_seconds: int
    overlap_candles: int

    def __post_init__(self) -> None:
        try:
            if (
                type(self.schema_version) is not int
                or self.schema_version != OPERATIONAL_MARKET_DATA_COLLECTOR_SCHEMA_VERSION
            ):
                raise ValueError

            if (
                type(self.collector_contract_version) is not int
                or self.collector_contract_version
                != OPERATIONAL_MARKET_DATA_COLLECTOR_CONTRACT_VERSION
            ):
                raise ValueError

            if self.scope is not OperationalMarketDataCollectorScope.BINANCE_SPOT_RAW:
                raise ValueError

            if not isinstance(self.targets, tuple):
                raise ValueError

            if not 1 <= len(self.targets) <= MAX_OPERATIONAL_MARKET_DATA_COLLECTOR_TARGETS:
                raise OperationalMarketDataCollectorBoundsExceededError()

            targets = tuple(_revalidate_target(item) for item in self.targets)
            keys = tuple(item.key for item in targets)

            if keys != tuple(sorted(keys)) or len(set(keys)) != len(keys):
                raise ValueError

            if type(self.interval_seconds) is not int:
                raise ValueError

            if (
                not 1
                <= self.interval_seconds
                <= (MAX_OPERATIONAL_MARKET_DATA_COLLECTOR_INTERVAL_SECONDS)
            ):
                raise OperationalMarketDataCollectorBoundsExceededError()

            if type(self.overlap_candles) is not int:
                raise ValueError

            if (
                not 0
                <= self.overlap_candles
                <= (MAX_OPERATIONAL_MARKET_DATA_COLLECTOR_OVERLAP_CANDLES)
            ):
                raise OperationalMarketDataCollectorBoundsExceededError()

        except OperationalMarketDataCollectorBoundsExceededError:
            raise
        except Exception:
            raise InvalidOperationalMarketDataCollectorSpecificationError() from None

        object.__setattr__(self, "targets", targets)


def _revalidate_specification(
    value: object,
) -> OperationalMarketDataCollectorSpecification:
    if not isinstance(value, OperationalMarketDataCollectorSpecification):
        raise InvalidOperationalMarketDataCollectorSpecificationError()

    return OperationalMarketDataCollectorSpecification(
        schema_version=value.schema_version,
        collector_contract_version=value.collector_contract_version,
        scope=value.scope,
        targets=value.targets,
        interval_seconds=value.interval_seconds,
        overlap_candles=value.overlap_candles,
    )


def operational_market_data_collector_specification_payload(
    specification: OperationalMarketDataCollectorSpecification,
) -> dict[str, object]:
    canonical = _revalidate_specification(specification)

    return {
        "schema_version": canonical.schema_version,
        "collector_contract_version": canonical.collector_contract_version,
        "scope": canonical.scope.value,
        "targets": [
            {
                "symbol": item.symbol,
                "timeframe": item.timeframe,
                "bootstrap_candles": item.bootstrap_candles,
            }
            for item in canonical.targets
        ],
        "interval_seconds": canonical.interval_seconds,
        "overlap_candles": canonical.overlap_candles,
    }


def operational_market_data_collector_specification_bytes(
    specification: OperationalMarketDataCollectorSpecification,
) -> bytes:
    return canonical_json_bytes(
        operational_market_data_collector_specification_payload(specification)
    )


def operational_market_data_collector_specification_checksum(
    specification: OperationalMarketDataCollectorSpecification,
) -> str:
    return hashlib.sha256(
        operational_market_data_collector_specification_bytes(specification)
    ).hexdigest()


def operational_market_data_collector_specifications_equal(
    left: OperationalMarketDataCollectorSpecification,
    right: OperationalMarketDataCollectorSpecification,
) -> bool:
    return operational_market_data_collector_specification_bytes(
        left
    ) == operational_market_data_collector_specification_bytes(right)


def validate_operational_market_data_collector_specification_checksum(
    specification: OperationalMarketDataCollectorSpecification,
    expected_checksum: object,
) -> OperationalMarketDataCollectorSpecification:
    canonical = _revalidate_specification(specification)

    try:
        checksum = _require_sha256(expected_checksum)
    except Exception:
        raise InvalidOperationalMarketDataCollectorSpecificationError() from None

    if operational_market_data_collector_specification_checksum(canonical) != checksum:
        raise OperationalMarketDataCollectorChecksumMismatchError()

    return canonical


@dataclass(frozen=True, slots=True)
class OperationalMarketDataCollectorStartIntent:
    specification_checksum: str

    def __post_init__(self) -> None:
        try:
            checksum = _require_sha256(self.specification_checksum)
        except Exception:
            raise InvalidOperationalMarketDataCollectorSpecificationError() from None

        object.__setattr__(self, "specification_checksum", checksum)


def operational_market_data_collector_start_intent_fingerprint(
    intent: OperationalMarketDataCollectorStartIntent,
) -> str:
    if not isinstance(intent, OperationalMarketDataCollectorStartIntent):
        raise InvalidOperationalMarketDataCollectorSpecificationError()

    canonical = OperationalMarketDataCollectorStartIntent(
        specification_checksum=intent.specification_checksum,
    )

    return hashlib.sha256(
        canonical_json_bytes(
            {
                "contract_version": (OPERATIONAL_MARKET_DATA_COLLECTOR_START_CONTRACT_VERSION),
                "specification_checksum": canonical.specification_checksum,
            }
        )
    ).hexdigest()


def operational_market_data_collector_epoch_checksum(
    epoch_id: UUID,
    specification_checksum: str,
) -> str:
    try:
        canonical_epoch_id = _require_uuid(epoch_id)
        canonical_specification_checksum = _require_sha256(specification_checksum)
    except Exception:
        raise InvalidOperationalMarketDataCollectorSpecificationError() from None

    return hashlib.sha256(
        canonical_json_bytes(
            {
                "epoch_id": str(canonical_epoch_id),
                "specification_checksum": canonical_specification_checksum,
            }
        )
    ).hexdigest()


def operational_market_data_collector_command_target(
    command_type: OperationalMarketDataCollectorCommandType,
) -> OperationalMarketDataCollectorDesiredState:
    if command_type is OperationalMarketDataCollectorCommandType.START:
        return OperationalMarketDataCollectorDesiredState.RUNNING
    if command_type is OperationalMarketDataCollectorCommandType.PAUSE:
        return OperationalMarketDataCollectorDesiredState.PAUSED
    if command_type is OperationalMarketDataCollectorCommandType.RESUME:
        return OperationalMarketDataCollectorDesiredState.RUNNING
    if command_type is OperationalMarketDataCollectorCommandType.STOP:
        return OperationalMarketDataCollectorDesiredState.STOPPED

    raise OperationalMarketDataCollectorCommandConflictError()


@dataclass(frozen=True, slots=True)
class OperationalMarketDataCollectorCommandIntent:
    epoch_id: UUID
    epoch_checksum: str
    command_type: OperationalMarketDataCollectorCommandType
    expected_record_version: int

    def __post_init__(self) -> None:
        try:
            epoch_id = _require_uuid(self.epoch_id)
            epoch_checksum = _require_sha256(self.epoch_checksum)

            if (
                not isinstance(
                    self.command_type,
                    OperationalMarketDataCollectorCommandType,
                )
                or self.command_type is OperationalMarketDataCollectorCommandType.START
            ):
                raise ValueError

            expected_record_version = _require_positive_bigint(self.expected_record_version)

        except OperationalMarketDataCollectorBoundsExceededError:
            raise
        except Exception:
            raise InvalidOperationalMarketDataCollectorSpecificationError() from None

        object.__setattr__(self, "epoch_id", epoch_id)
        object.__setattr__(self, "epoch_checksum", epoch_checksum)
        object.__setattr__(
            self,
            "expected_record_version",
            expected_record_version,
        )


def operational_market_data_collector_command_intent_fingerprint(
    intent: OperationalMarketDataCollectorCommandIntent,
) -> str:
    if not isinstance(intent, OperationalMarketDataCollectorCommandIntent):
        raise InvalidOperationalMarketDataCollectorSpecificationError()

    canonical = OperationalMarketDataCollectorCommandIntent(
        epoch_id=intent.epoch_id,
        epoch_checksum=intent.epoch_checksum,
        command_type=intent.command_type,
        expected_record_version=intent.expected_record_version,
    )

    return hashlib.sha256(
        canonical_json_bytes(
            {
                "contract_version": (OPERATIONAL_MARKET_DATA_COLLECTOR_COMMAND_CONTRACT_VERSION),
                "epoch_id": str(canonical.epoch_id),
                "epoch_checksum": canonical.epoch_checksum,
                "command_type": canonical.command_type.value,
                "expected_record_version": canonical.expected_record_version,
            }
        )
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class OperationalMarketDataCollectorWorkerClaim:
    epoch_id: UUID
    worker_id: UUID = field(repr=False)
    fencing_token: int
    claimed_at: datetime
    heartbeat_at: datetime
    lease_expires_at: datetime

    def __post_init__(self) -> None:
        try:
            epoch_id = _require_uuid(self.epoch_id)
            worker_id = _require_uuid(self.worker_id)
            fencing_token = _require_positive_bigint(self.fencing_token)
            claimed_at = _require_utc(self.claimed_at)
            heartbeat_at = _require_utc(self.heartbeat_at)
            lease_expires_at = _require_utc(self.lease_expires_at)

            if not claimed_at <= heartbeat_at < lease_expires_at:
                raise ValueError

        except OperationalMarketDataCollectorBoundsExceededError:
            raise
        except Exception:
            raise OperationalMarketDataCollectorLeaseError() from None

        object.__setattr__(self, "epoch_id", epoch_id)
        object.__setattr__(self, "worker_id", worker_id)
        object.__setattr__(self, "fencing_token", fencing_token)
        object.__setattr__(self, "claimed_at", claimed_at)
        object.__setattr__(self, "heartbeat_at", heartbeat_at)
        object.__setattr__(self, "lease_expires_at", lease_expires_at)

    def belongs_to(
        self,
        worker_id: UUID,
        fencing_token: int,
    ) -> bool:
        try:
            canonical_worker_id = _require_uuid(worker_id)
            canonical_fencing_token = _require_positive_bigint(fencing_token)
        except Exception:
            return False

        return (
            self.worker_id == canonical_worker_id and self.fencing_token == canonical_fencing_token
        )

    def is_active(self, now: datetime) -> bool:
        try:
            current = _require_utc(now)
        except Exception:
            raise OperationalMarketDataCollectorLeaseError() from None

        if current < self.heartbeat_at:
            raise OperationalMarketDataCollectorLeaseError()

        return current < self.lease_expires_at

    def is_expired(self, now: datetime) -> bool:
        return not self.is_active(now)


@dataclass(frozen=True, slots=True)
class OperationalMarketDataCollectorFailure:
    code: OperationalMarketDataCollectorFailureCode
    failed_at: datetime

    def __post_init__(self) -> None:
        try:
            if not isinstance(self.code, OperationalMarketDataCollectorFailureCode):
                raise ValueError

            failed_at = _require_utc(self.failed_at)

        except Exception:
            raise InvalidOperationalMarketDataCollectorSpecificationError() from None

        object.__setattr__(self, "failed_at", failed_at)


@dataclass(frozen=True, slots=True)
class OperationalMarketDataCollectorCommand:
    command_id: UUID
    command_contract_version: int
    epoch_id: UUID
    epoch_checksum: str
    command_type: OperationalMarketDataCollectorCommandType
    desired_state: OperationalMarketDataCollectorDesiredState
    expected_record_version: int | None
    resulting_record_version: int
    actor_id: UUID
    requested_at: datetime
    idempotency_key: str = field(repr=False)
    intent_fingerprint: str = field(repr=False)

    def __post_init__(self) -> None:
        try:
            command_id = _require_uuid(self.command_id)

            if (
                type(self.command_contract_version) is not int
                or self.command_contract_version
                != OPERATIONAL_MARKET_DATA_COLLECTOR_COMMAND_CONTRACT_VERSION
            ):
                raise ValueError

            epoch_id = _require_uuid(self.epoch_id)
            epoch_checksum = _require_sha256(self.epoch_checksum)

            if not isinstance(
                self.command_type,
                OperationalMarketDataCollectorCommandType,
            ):
                raise ValueError

            if not isinstance(
                self.desired_state,
                OperationalMarketDataCollectorDesiredState,
            ):
                raise ValueError

            expected_target = operational_market_data_collector_command_target(self.command_type)

            if self.desired_state is not expected_target:
                raise ValueError

            resulting_record_version = _require_positive_bigint(self.resulting_record_version)

            if self.command_type is OperationalMarketDataCollectorCommandType.START:
                if self.expected_record_version is not None:
                    raise ValueError
                if resulting_record_version != 1:
                    raise ValueError
                expected_record_version = None
            else:
                expected_record_version = _require_positive_bigint(self.expected_record_version)

                if resulting_record_version != expected_record_version + 1:
                    raise ValueError

            actor_id = _require_uuid(self.actor_id)
            requested_at = _require_utc(self.requested_at)

            idempotency_key = validate_operational_market_data_collector_idempotency_key(
                self.idempotency_key
            )

            intent_fingerprint = _require_sha256(self.intent_fingerprint)

            if self.command_type is not OperationalMarketDataCollectorCommandType.START:
                if expected_record_version is None:
                    raise ValueError

                expected_fingerprint = operational_market_data_collector_command_intent_fingerprint(
                    OperationalMarketDataCollectorCommandIntent(
                        epoch_id=epoch_id,
                        epoch_checksum=epoch_checksum,
                        command_type=self.command_type,
                        expected_record_version=expected_record_version,
                    )
                )

                if intent_fingerprint != expected_fingerprint:
                    raise ValueError

        except OperationalMarketDataCollectorBoundsExceededError:
            raise
        except Exception:
            raise InvalidOperationalMarketDataCollectorSpecificationError() from None

        object.__setattr__(self, "command_id", command_id)
        object.__setattr__(self, "epoch_id", epoch_id)
        object.__setattr__(self, "epoch_checksum", epoch_checksum)
        object.__setattr__(
            self,
            "expected_record_version",
            expected_record_version,
        )
        object.__setattr__(
            self,
            "resulting_record_version",
            resulting_record_version,
        )
        object.__setattr__(self, "actor_id", actor_id)
        object.__setattr__(self, "requested_at", requested_at)
        object.__setattr__(self, "idempotency_key", idempotency_key)
        object.__setattr__(self, "intent_fingerprint", intent_fingerprint)


@dataclass(frozen=True, slots=True)
class OperationalMarketDataCollectorEpoch:
    epoch_id: UUID
    schema_version: int
    collector_contract_version: int
    scope: OperationalMarketDataCollectorScope
    specification: OperationalMarketDataCollectorSpecification
    specification_checksum: str
    desired_state: OperationalMarketDataCollectorDesiredState
    observed_state: OperationalMarketDataCollectorObservedState
    record_version: int
    fencing_token: int
    epoch_checksum: str
    start_requested_by: UUID
    start_requested_at: datetime
    start_idempotency_key: str = field(repr=False)
    start_intent_fingerprint: str = field(repr=False)
    worker_claim: OperationalMarketDataCollectorWorkerClaim | None = None
    failure: OperationalMarketDataCollectorFailure | None = None
    terminal_at: datetime | None = None

    def __post_init__(self) -> None:
        try:
            epoch_id = _require_uuid(self.epoch_id)
            specification = _revalidate_specification(self.specification)

            if self.schema_version != specification.schema_version:
                raise ValueError

            if self.collector_contract_version != specification.collector_contract_version:
                raise ValueError

            if self.scope is not specification.scope:
                raise ValueError

            specification_checksum = _require_sha256(self.specification_checksum)

            validate_operational_market_data_collector_specification_checksum(
                specification,
                specification_checksum,
            )

            if not isinstance(
                self.desired_state,
                OperationalMarketDataCollectorDesiredState,
            ):
                raise ValueError

            if not isinstance(
                self.observed_state,
                OperationalMarketDataCollectorObservedState,
            ):
                raise ValueError

            record_version = _require_positive_bigint(self.record_version)
            fencing_token = _require_nonnegative_bigint(self.fencing_token)
            epoch_checksum = _require_sha256(self.epoch_checksum)

            expected_epoch_checksum = operational_market_data_collector_epoch_checksum(
                epoch_id,
                specification_checksum,
            )

            if epoch_checksum != expected_epoch_checksum:
                raise OperationalMarketDataCollectorChecksumMismatchError()

            start_requested_by = _require_uuid(self.start_requested_by)
            start_requested_at = _require_utc(self.start_requested_at)

            start_idempotency_key = validate_operational_market_data_collector_idempotency_key(
                self.start_idempotency_key
            )

            start_intent_fingerprint = _require_sha256(self.start_intent_fingerprint)

            expected_start_fingerprint = operational_market_data_collector_start_intent_fingerprint(
                OperationalMarketDataCollectorStartIntent(
                    specification_checksum=specification_checksum
                )
            )

            if start_intent_fingerprint != expected_start_fingerprint:
                raise ValueError

            worker_claim = (
                None
                if self.worker_claim is None
                else OperationalMarketDataCollectorWorkerClaim(
                    epoch_id=self.worker_claim.epoch_id,
                    worker_id=self.worker_claim.worker_id,
                    fencing_token=self.worker_claim.fencing_token,
                    claimed_at=self.worker_claim.claimed_at,
                    heartbeat_at=self.worker_claim.heartbeat_at,
                    lease_expires_at=self.worker_claim.lease_expires_at,
                )
            )

            failure = (
                None
                if self.failure is None
                else OperationalMarketDataCollectorFailure(
                    code=self.failure.code,
                    failed_at=self.failure.failed_at,
                )
            )

            terminal_at = None if self.terminal_at is None else _require_utc(self.terminal_at)

            if worker_claim is not None:
                if worker_claim.epoch_id != epoch_id:
                    raise ValueError

                if worker_claim.fencing_token != fencing_token:
                    raise ValueError

            claim_required_states = {
                OperationalMarketDataCollectorObservedState.STARTING,
                OperationalMarketDataCollectorObservedState.RUNNING,
                OperationalMarketDataCollectorObservedState.RECOVERING,
                OperationalMarketDataCollectorObservedState.STOPPING,
            }

            if self.observed_state in claim_required_states:
                if worker_claim is None:
                    raise ValueError
            elif worker_claim is not None:
                raise ValueError

            if self.observed_state is OperationalMarketDataCollectorObservedState.FAILED:
                if (
                    failure is None
                    or terminal_at is None
                    or failure.failed_at != terminal_at
                    or worker_claim is not None
                ):
                    raise ValueError

            elif self.observed_state is OperationalMarketDataCollectorObservedState.STOPPED:
                if (
                    self.desired_state is not OperationalMarketDataCollectorDesiredState.STOPPED
                    or failure is not None
                    or terminal_at is None
                    or worker_claim is not None
                ):
                    raise ValueError

            elif failure is not None or terminal_at is not None:
                raise ValueError

            if terminal_at is not None and terminal_at < start_requested_at:
                raise ValueError

        except OperationalMarketDataCollectorBoundsExceededError:
            raise
        except OperationalMarketDataCollectorChecksumMismatchError:
            raise
        except OperationalMarketDataCollectorLeaseError:
            raise
        except Exception:
            raise InvalidOperationalMarketDataCollectorSpecificationError() from None

        object.__setattr__(self, "epoch_id", epoch_id)
        object.__setattr__(self, "specification", specification)
        object.__setattr__(
            self,
            "specification_checksum",
            specification_checksum,
        )
        object.__setattr__(self, "record_version", record_version)
        object.__setattr__(self, "fencing_token", fencing_token)
        object.__setattr__(self, "epoch_checksum", epoch_checksum)
        object.__setattr__(self, "start_requested_by", start_requested_by)
        object.__setattr__(self, "start_requested_at", start_requested_at)
        object.__setattr__(
            self,
            "start_idempotency_key",
            start_idempotency_key,
        )
        object.__setattr__(
            self,
            "start_intent_fingerprint",
            start_intent_fingerprint,
        )
        object.__setattr__(self, "worker_claim", worker_claim)
        object.__setattr__(self, "failure", failure)
        object.__setattr__(self, "terminal_at", terminal_at)


def _revalidate_epoch(
    value: object,
) -> OperationalMarketDataCollectorEpoch:
    if not isinstance(value, OperationalMarketDataCollectorEpoch):
        raise InvalidOperationalMarketDataCollectorSpecificationError()

    return replace(value)


def is_operational_market_data_collector_transition_allowed(
    current: OperationalMarketDataCollectorObservedState,
    target: OperationalMarketDataCollectorObservedState,
) -> bool:
    if not isinstance(current, OperationalMarketDataCollectorObservedState):
        return False
    if not isinstance(target, OperationalMarketDataCollectorObservedState):
        return False

    return target in _ALLOWED_OBSERVED_TRANSITIONS[current]


def require_operational_market_data_collector_transition(
    current: OperationalMarketDataCollectorObservedState,
    target: OperationalMarketDataCollectorObservedState,
) -> None:
    if not is_operational_market_data_collector_transition_allowed(current, target):
        raise OperationalMarketDataCollectorStateTransitionConflictError()


def operational_market_data_collector_epoch_is_terminal(
    epoch: OperationalMarketDataCollectorEpoch,
) -> bool:
    canonical = _revalidate_epoch(epoch)

    return canonical.observed_state in TERMINAL_OPERATIONAL_MARKET_DATA_COLLECTOR_STATES


def start_operational_market_data_collector_epoch(
    *,
    epoch_id: UUID,
    command_id: UUID,
    specification: OperationalMarketDataCollectorSpecification,
    start_intent: OperationalMarketDataCollectorStartIntent,
    requested_by: UUID,
    requested_at: datetime,
    idempotency_key: str,
) -> tuple[
    OperationalMarketDataCollectorEpoch,
    OperationalMarketDataCollectorCommand,
]:
    canonical = _revalidate_specification(specification)

    if not isinstance(start_intent, OperationalMarketDataCollectorStartIntent):
        raise InvalidOperationalMarketDataCollectorSpecificationError()

    intent = OperationalMarketDataCollectorStartIntent(
        specification_checksum=start_intent.specification_checksum,
    )

    specification_checksum = operational_market_data_collector_specification_checksum(canonical)

    if intent.specification_checksum != specification_checksum:
        raise OperationalMarketDataCollectorCommandConflictError()

    try:
        epoch_id = _require_uuid(epoch_id)
        command_id = _require_uuid(command_id)
        requested_by = _require_uuid(requested_by)
        requested_at = _require_utc(requested_at)
    except Exception:
        raise InvalidOperationalMarketDataCollectorSpecificationError() from None

    idempotency_key = validate_operational_market_data_collector_idempotency_key(idempotency_key)

    epoch_checksum = operational_market_data_collector_epoch_checksum(
        epoch_id,
        specification_checksum,
    )

    fingerprint = operational_market_data_collector_start_intent_fingerprint(intent)

    epoch = OperationalMarketDataCollectorEpoch(
        epoch_id=epoch_id,
        schema_version=canonical.schema_version,
        collector_contract_version=canonical.collector_contract_version,
        scope=canonical.scope,
        specification=canonical,
        specification_checksum=specification_checksum,
        desired_state=OperationalMarketDataCollectorDesiredState.RUNNING,
        observed_state=OperationalMarketDataCollectorObservedState.PENDING,
        record_version=1,
        fencing_token=0,
        epoch_checksum=epoch_checksum,
        start_requested_by=requested_by,
        start_requested_at=requested_at,
        start_idempotency_key=idempotency_key,
        start_intent_fingerprint=fingerprint,
    )

    command = OperationalMarketDataCollectorCommand(
        command_id=command_id,
        command_contract_version=(OPERATIONAL_MARKET_DATA_COLLECTOR_COMMAND_CONTRACT_VERSION),
        epoch_id=epoch.epoch_id,
        epoch_checksum=epoch.epoch_checksum,
        command_type=OperationalMarketDataCollectorCommandType.START,
        desired_state=OperationalMarketDataCollectorDesiredState.RUNNING,
        expected_record_version=None,
        resulting_record_version=1,
        actor_id=requested_by,
        requested_at=requested_at,
        idempotency_key=idempotency_key,
        intent_fingerprint=fingerprint,
    )

    return epoch, command


def is_operational_market_data_collector_command_allowed(
    epoch: OperationalMarketDataCollectorEpoch,
    command_type: OperationalMarketDataCollectorCommandType,
) -> bool:
    try:
        canonical = _revalidate_epoch(epoch)
    except Exception:
        return False

    if not isinstance(command_type, OperationalMarketDataCollectorCommandType):
        return False

    if command_type is OperationalMarketDataCollectorCommandType.START:
        return False

    if operational_market_data_collector_epoch_is_terminal(canonical):
        return False

    return command_type in _ALLOWED_DESIRED_COMMANDS[canonical.desired_state]


def request_operational_market_data_collector_command(
    epoch: OperationalMarketDataCollectorEpoch,
    *,
    command_id: UUID,
    intent: OperationalMarketDataCollectorCommandIntent,
    actor_id: UUID,
    requested_at: datetime,
    idempotency_key: str,
) -> tuple[
    OperationalMarketDataCollectorEpoch,
    OperationalMarketDataCollectorCommand,
]:
    canonical = _revalidate_epoch(epoch)

    if operational_market_data_collector_epoch_is_terminal(canonical):
        raise OperationalMarketDataCollectorCommandConflictError()

    if not isinstance(intent, OperationalMarketDataCollectorCommandIntent):
        raise InvalidOperationalMarketDataCollectorSpecificationError()

    command_intent = OperationalMarketDataCollectorCommandIntent(
        epoch_id=intent.epoch_id,
        epoch_checksum=intent.epoch_checksum,
        command_type=intent.command_type,
        expected_record_version=intent.expected_record_version,
    )

    if (
        command_intent.epoch_id != canonical.epoch_id
        or command_intent.epoch_checksum != canonical.epoch_checksum
        or command_intent.expected_record_version != canonical.record_version
    ):
        raise OperationalMarketDataCollectorCommandConflictError()

    if not is_operational_market_data_collector_command_allowed(
        canonical,
        command_intent.command_type,
    ):
        raise OperationalMarketDataCollectorCommandConflictError()

    try:
        command_id = _require_uuid(command_id)
        actor_id = _require_uuid(actor_id)
        requested_at = _require_utc(requested_at)
    except Exception:
        raise InvalidOperationalMarketDataCollectorSpecificationError() from None

    if requested_at < canonical.start_requested_at:
        raise InvalidOperationalMarketDataCollectorSpecificationError()

    idempotency_key = validate_operational_market_data_collector_idempotency_key(idempotency_key)

    resulting_record_version = _next_record_version(canonical.record_version)

    desired_state = operational_market_data_collector_command_target(command_intent.command_type)

    fingerprint = operational_market_data_collector_command_intent_fingerprint(command_intent)

    updated = replace(
        canonical,
        desired_state=desired_state,
        record_version=resulting_record_version,
    )

    command = OperationalMarketDataCollectorCommand(
        command_id=command_id,
        command_contract_version=(OPERATIONAL_MARKET_DATA_COLLECTOR_COMMAND_CONTRACT_VERSION),
        epoch_id=canonical.epoch_id,
        epoch_checksum=canonical.epoch_checksum,
        command_type=command_intent.command_type,
        desired_state=desired_state,
        expected_record_version=canonical.record_version,
        resulting_record_version=resulting_record_version,
        actor_id=actor_id,
        requested_at=requested_at,
        idempotency_key=idempotency_key,
        intent_fingerprint=fingerprint,
    )

    return updated, command


def _require_current_active_claim(
    epoch: OperationalMarketDataCollectorEpoch,
    *,
    worker_id: UUID,
    fencing_token: int,
    now: datetime,
) -> OperationalMarketDataCollectorWorkerClaim:
    claim = epoch.worker_claim

    if claim is None or not claim.belongs_to(worker_id, fencing_token) or not claim.is_active(now):
        raise OperationalMarketDataCollectorLeaseError()

    return claim


def claim_operational_market_data_collector_epoch(
    epoch: OperationalMarketDataCollectorEpoch,
    *,
    worker_id: UUID,
    claimed_at: datetime,
    lease_expires_at: datetime,
) -> OperationalMarketDataCollectorEpoch:
    canonical = _revalidate_epoch(epoch)

    if operational_market_data_collector_epoch_is_terminal(canonical):
        raise OperationalMarketDataCollectorCommandConflictError()

    if (
        canonical.desired_state is not OperationalMarketDataCollectorDesiredState.RUNNING
        or canonical.observed_state
        not in {
            OperationalMarketDataCollectorObservedState.PENDING,
            OperationalMarketDataCollectorObservedState.PAUSED,
        }
        or canonical.worker_claim is not None
    ):
        raise OperationalMarketDataCollectorLeaseError()

    try:
        worker_id = _require_uuid(worker_id)
        claimed_at = _require_utc(claimed_at)
        lease_expires_at = _require_utc(lease_expires_at)
    except Exception:
        raise OperationalMarketDataCollectorLeaseError() from None

    if claimed_at < canonical.start_requested_at:
        raise OperationalMarketDataCollectorLeaseError()

    if lease_expires_at <= claimed_at:
        raise OperationalMarketDataCollectorLeaseError()

    fencing_token = _next_fencing_token(canonical.fencing_token)

    claim = OperationalMarketDataCollectorWorkerClaim(
        epoch_id=canonical.epoch_id,
        worker_id=worker_id,
        fencing_token=fencing_token,
        claimed_at=claimed_at,
        heartbeat_at=claimed_at,
        lease_expires_at=lease_expires_at,
    )

    require_operational_market_data_collector_transition(
        canonical.observed_state,
        OperationalMarketDataCollectorObservedState.STARTING,
    )

    return replace(
        canonical,
        observed_state=OperationalMarketDataCollectorObservedState.STARTING,
        record_version=_next_record_version(canonical.record_version),
        fencing_token=fencing_token,
        worker_claim=claim,
    )


def renew_operational_market_data_collector_worker_claim(
    epoch: OperationalMarketDataCollectorEpoch,
    *,
    worker_id: UUID,
    fencing_token: int,
    heartbeat_at: datetime,
    lease_expires_at: datetime,
) -> OperationalMarketDataCollectorEpoch:
    canonical = _revalidate_epoch(epoch)

    claim = _require_current_active_claim(
        canonical,
        worker_id=worker_id,
        fencing_token=fencing_token,
        now=heartbeat_at,
    )

    heartbeat_at = _require_utc(heartbeat_at)
    lease_expires_at = _require_utc(lease_expires_at)

    if heartbeat_at <= claim.heartbeat_at:
        raise OperationalMarketDataCollectorLeaseError()

    if lease_expires_at <= heartbeat_at:
        raise OperationalMarketDataCollectorLeaseError()

    if lease_expires_at <= claim.lease_expires_at:
        raise OperationalMarketDataCollectorLeaseError()

    renewed = OperationalMarketDataCollectorWorkerClaim(
        epoch_id=claim.epoch_id,
        worker_id=claim.worker_id,
        fencing_token=claim.fencing_token,
        claimed_at=claim.claimed_at,
        heartbeat_at=heartbeat_at,
        lease_expires_at=lease_expires_at,
    )

    return replace(
        canonical,
        record_version=_next_record_version(canonical.record_version),
        worker_claim=renewed,
    )


def recover_operational_market_data_collector_epoch(
    epoch: OperationalMarketDataCollectorEpoch,
    *,
    worker_id: UUID,
    recovered_at: datetime,
    lease_expires_at: datetime,
) -> OperationalMarketDataCollectorEpoch:
    canonical = _revalidate_epoch(epoch)

    if operational_market_data_collector_epoch_is_terminal(canonical):
        raise OperationalMarketDataCollectorCommandConflictError()

    previous_claim = canonical.worker_claim

    if previous_claim is None:
        raise OperationalMarketDataCollectorLeaseError()

    if canonical.observed_state not in {
        OperationalMarketDataCollectorObservedState.STARTING,
        OperationalMarketDataCollectorObservedState.RUNNING,
        OperationalMarketDataCollectorObservedState.RECOVERING,
        OperationalMarketDataCollectorObservedState.STOPPING,
    }:
        raise OperationalMarketDataCollectorLeaseError()

    try:
        recovered_at = _require_utc(recovered_at)
    except Exception:
        raise OperationalMarketDataCollectorLeaseError() from None

    if not previous_claim.is_expired(recovered_at):
        raise OperationalMarketDataCollectorLeaseError()

    try:
        worker_id = _require_uuid(worker_id)
        lease_expires_at = _require_utc(lease_expires_at)
    except Exception:
        raise OperationalMarketDataCollectorLeaseError() from None

    if lease_expires_at <= recovered_at:
        raise OperationalMarketDataCollectorLeaseError()

    fencing_token = _next_fencing_token(canonical.fencing_token)

    recovered_claim = OperationalMarketDataCollectorWorkerClaim(
        epoch_id=canonical.epoch_id,
        worker_id=worker_id,
        fencing_token=fencing_token,
        claimed_at=recovered_at,
        heartbeat_at=recovered_at,
        lease_expires_at=lease_expires_at,
    )

    if canonical.observed_state is not OperationalMarketDataCollectorObservedState.RECOVERING:
        require_operational_market_data_collector_transition(
            canonical.observed_state,
            OperationalMarketDataCollectorObservedState.RECOVERING,
        )

    return replace(
        canonical,
        observed_state=OperationalMarketDataCollectorObservedState.RECOVERING,
        record_version=_next_record_version(canonical.record_version),
        fencing_token=fencing_token,
        worker_claim=recovered_claim,
    )


def mark_operational_market_data_collector_epoch_starting(
    epoch: OperationalMarketDataCollectorEpoch,
    *,
    worker_id: UUID,
    fencing_token: int,
    observed_at: datetime,
) -> OperationalMarketDataCollectorEpoch:
    canonical = _revalidate_epoch(epoch)

    if (
        canonical.observed_state is not OperationalMarketDataCollectorObservedState.RECOVERING
        or canonical.desired_state is not OperationalMarketDataCollectorDesiredState.RUNNING
    ):
        raise OperationalMarketDataCollectorStateTransitionConflictError()

    _require_current_active_claim(
        canonical,
        worker_id=worker_id,
        fencing_token=fencing_token,
        now=observed_at,
    )

    require_operational_market_data_collector_transition(
        canonical.observed_state,
        OperationalMarketDataCollectorObservedState.STARTING,
    )

    return replace(
        canonical,
        observed_state=OperationalMarketDataCollectorObservedState.STARTING,
        record_version=_next_record_version(canonical.record_version),
    )


def mark_operational_market_data_collector_epoch_running(
    epoch: OperationalMarketDataCollectorEpoch,
    *,
    worker_id: UUID,
    fencing_token: int,
    observed_at: datetime,
) -> OperationalMarketDataCollectorEpoch:
    canonical = _revalidate_epoch(epoch)

    if (
        canonical.observed_state is not OperationalMarketDataCollectorObservedState.STARTING
        or canonical.desired_state is not OperationalMarketDataCollectorDesiredState.RUNNING
    ):
        raise OperationalMarketDataCollectorStateTransitionConflictError()

    _require_current_active_claim(
        canonical,
        worker_id=worker_id,
        fencing_token=fencing_token,
        now=observed_at,
    )

    require_operational_market_data_collector_transition(
        canonical.observed_state,
        OperationalMarketDataCollectorObservedState.RUNNING,
    )

    return replace(
        canonical,
        observed_state=OperationalMarketDataCollectorObservedState.RUNNING,
        record_version=_next_record_version(canonical.record_version),
    )


def mark_operational_market_data_collector_epoch_stopping(
    epoch: OperationalMarketDataCollectorEpoch,
    *,
    worker_id: UUID,
    fencing_token: int,
    observed_at: datetime,
) -> OperationalMarketDataCollectorEpoch:
    canonical = _revalidate_epoch(epoch)

    if (
        canonical.desired_state is not OperationalMarketDataCollectorDesiredState.STOPPED
        or canonical.observed_state
        not in {
            OperationalMarketDataCollectorObservedState.STARTING,
            OperationalMarketDataCollectorObservedState.RUNNING,
            OperationalMarketDataCollectorObservedState.RECOVERING,
        }
    ):
        raise OperationalMarketDataCollectorStateTransitionConflictError()

    _require_current_active_claim(
        canonical,
        worker_id=worker_id,
        fencing_token=fencing_token,
        now=observed_at,
    )

    require_operational_market_data_collector_transition(
        canonical.observed_state,
        OperationalMarketDataCollectorObservedState.STOPPING,
    )

    return replace(
        canonical,
        observed_state=OperationalMarketDataCollectorObservedState.STOPPING,
        record_version=_next_record_version(canonical.record_version),
    )


def settle_operational_market_data_collector_epoch_paused(
    epoch: OperationalMarketDataCollectorEpoch,
    *,
    worker_id: UUID,
    fencing_token: int,
    observed_at: datetime,
) -> OperationalMarketDataCollectorEpoch:
    canonical = _revalidate_epoch(epoch)

    if (
        canonical.desired_state is not OperationalMarketDataCollectorDesiredState.PAUSED
        or canonical.observed_state
        not in {
            OperationalMarketDataCollectorObservedState.STARTING,
            OperationalMarketDataCollectorObservedState.RUNNING,
            OperationalMarketDataCollectorObservedState.RECOVERING,
        }
    ):
        raise OperationalMarketDataCollectorStateTransitionConflictError()

    _require_current_active_claim(
        canonical,
        worker_id=worker_id,
        fencing_token=fencing_token,
        now=observed_at,
    )

    require_operational_market_data_collector_transition(
        canonical.observed_state,
        OperationalMarketDataCollectorObservedState.PAUSED,
    )

    return replace(
        canonical,
        observed_state=OperationalMarketDataCollectorObservedState.PAUSED,
        record_version=_next_record_version(canonical.record_version),
        worker_claim=None,
    )


def settle_operational_market_data_collector_epoch_stopped(
    epoch: OperationalMarketDataCollectorEpoch,
    *,
    worker_id: UUID,
    fencing_token: int,
    observed_at: datetime,
) -> OperationalMarketDataCollectorEpoch:
    canonical = _revalidate_epoch(epoch)

    if (
        canonical.desired_state is not OperationalMarketDataCollectorDesiredState.STOPPED
        or canonical.observed_state
        not in {
            OperationalMarketDataCollectorObservedState.STOPPING,
            OperationalMarketDataCollectorObservedState.RECOVERING,
        }
    ):
        raise OperationalMarketDataCollectorStateTransitionConflictError()

    _require_current_active_claim(
        canonical,
        worker_id=worker_id,
        fencing_token=fencing_token,
        now=observed_at,
    )

    require_operational_market_data_collector_transition(
        canonical.observed_state,
        OperationalMarketDataCollectorObservedState.STOPPED,
    )

    observed_at = _require_utc(observed_at)

    return replace(
        canonical,
        observed_state=OperationalMarketDataCollectorObservedState.STOPPED,
        record_version=_next_record_version(canonical.record_version),
        worker_claim=None,
        terminal_at=observed_at,
    )


def settle_unclaimed_operational_market_data_collector_epoch(
    epoch: OperationalMarketDataCollectorEpoch,
    *,
    observed_at: datetime,
) -> OperationalMarketDataCollectorEpoch:
    canonical = _revalidate_epoch(epoch)

    if canonical.worker_claim is not None:
        raise OperationalMarketDataCollectorLeaseError()

    try:
        observed_at = _require_utc(observed_at)
    except Exception:
        raise InvalidOperationalMarketDataCollectorSpecificationError() from None

    if canonical.desired_state is OperationalMarketDataCollectorDesiredState.PAUSED:
        target_state = OperationalMarketDataCollectorObservedState.PAUSED
        terminal_at = None

    elif canonical.desired_state is OperationalMarketDataCollectorDesiredState.STOPPED:
        target_state = OperationalMarketDataCollectorObservedState.STOPPED
        terminal_at = observed_at

    else:
        raise OperationalMarketDataCollectorStateTransitionConflictError()

    require_operational_market_data_collector_transition(
        canonical.observed_state,
        target_state,
    )

    return replace(
        canonical,
        observed_state=target_state,
        record_version=_next_record_version(canonical.record_version),
        terminal_at=terminal_at,
    )


def fail_unclaimed_operational_market_data_collector_epoch(
    epoch: OperationalMarketDataCollectorEpoch,
    *,
    failure_code: OperationalMarketDataCollectorFailureCode,
    failed_at: datetime,
) -> OperationalMarketDataCollectorEpoch:
    canonical = _revalidate_epoch(epoch)

    if canonical.worker_claim is not None:
        raise OperationalMarketDataCollectorLeaseError()

    if operational_market_data_collector_epoch_is_terminal(canonical):
        raise OperationalMarketDataCollectorStateTransitionConflictError()

    if not isinstance(
        failure_code,
        OperationalMarketDataCollectorFailureCode,
    ):
        raise InvalidOperationalMarketDataCollectorSpecificationError()

    try:
        failed_at = _require_utc(failed_at)
    except Exception:
        raise InvalidOperationalMarketDataCollectorSpecificationError() from None

    require_operational_market_data_collector_transition(
        canonical.observed_state,
        OperationalMarketDataCollectorObservedState.FAILED,
    )

    failure = OperationalMarketDataCollectorFailure(
        code=failure_code,
        failed_at=failed_at,
    )

    return replace(
        canonical,
        observed_state=OperationalMarketDataCollectorObservedState.FAILED,
        record_version=_next_record_version(canonical.record_version),
        failure=failure,
        terminal_at=failed_at,
    )


def fail_claimed_operational_market_data_collector_epoch(
    epoch: OperationalMarketDataCollectorEpoch,
    *,
    worker_id: UUID,
    fencing_token: int,
    failure_code: OperationalMarketDataCollectorFailureCode,
    failed_at: datetime,
) -> OperationalMarketDataCollectorEpoch:
    canonical = _revalidate_epoch(epoch)

    if operational_market_data_collector_epoch_is_terminal(canonical):
        raise OperationalMarketDataCollectorStateTransitionConflictError()

    if not isinstance(
        failure_code,
        OperationalMarketDataCollectorFailureCode,
    ):
        raise InvalidOperationalMarketDataCollectorSpecificationError()

    _require_current_active_claim(
        canonical,
        worker_id=worker_id,
        fencing_token=fencing_token,
        now=failed_at,
    )

    failed_at = _require_utc(failed_at)

    require_operational_market_data_collector_transition(
        canonical.observed_state,
        OperationalMarketDataCollectorObservedState.FAILED,
    )

    failure = OperationalMarketDataCollectorFailure(
        code=failure_code,
        failed_at=failed_at,
    )

    return replace(
        canonical,
        observed_state=OperationalMarketDataCollectorObservedState.FAILED,
        record_version=_next_record_version(canonical.record_version),
        worker_claim=None,
        failure=failure,
        terminal_at=failed_at,
    )
