"""Pure operational paper-session profile domain contracts."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from copy import copy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Final
from uuid import UUID

from app.backtesting.domain import (
    ExecutionAssumptions,
    FeeModel,
    InstrumentConstraints,
    IntrabarPolicy,
    PositionSizedExecutionAssumptions,
    PositionSizingPolicy,
    RiskLimits,
    SlippageKind,
    SlippageModel,
    StopLossPolicy,
    StopLossRiskLimits,
    StrategyDescriptor,
    StrategyParameters,
)
from app.backtesting.serialization import canonical_json_bytes, canonical_value, decimal_text
from app.domain.errors import DomainError
from app.indicators.regime import MarketRegimePolicy
from app.market_data.domain import Timeframe
from app.market_data.timeframes import TIMEFRAMES
from app.operational_mandates import (
    OperationalMandateInstrument,
    require_operational_mandate_capability,
)
from app.operational_paper_session_profiles.errors import (
    InvalidOperationalPaperSessionProfileSpecificationError,
    InvalidOperationalPaperSessionProfileStrategySnapshotError,
    OperationalPaperSessionProfileBoundsExceededError,
    OperationalPaperSessionProfileChecksumMismatchError,
    OperationalPaperSessionProfileStateTransitionConflictError,
)

OPERATIONAL_PAPER_SESSION_PROFILE_SPEC_SCHEMA_VERSION: Final = 1
OPERATIONAL_PAPER_SESSION_PROFILE_CREATE_CONTRACT_VERSION: Final = 1
STRATEGY_SNAPSHOT_SCHEMA_VERSION: Final = 1
MAX_OPERATIONAL_PAPER_SESSION_PROFILE_NAME_LENGTH: Final = 120
MAX_OPERATIONAL_PAPER_SESSION_PROFILE_DESCRIPTION_LENGTH: Final = 1_000
MAX_OPERATIONAL_PAPER_SESSION_PROFILE_IDEMPOTENCY_KEY_LENGTH: Final = 128
MAX_OPERATIONAL_PAPER_SESSION_PROFILE_WARMUP_CANDLES: Final = 100_000
MAX_OPERATIONAL_PAPER_SESSION_PROFILE_HISTORY_WINDOW: Final = 100_000
MAX_OPERATIONAL_PAPER_SESSION_PROFILE_CANDLES: Final = 2_000_000
MAX_OPERATIONAL_PAPER_SESSION_PROFILE_ORDERS: Final = 1_000_000
MAX_OPERATIONAL_PAPER_SESSION_PROFILE_EVENTS: Final = 20_000_000

_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class OperationalPaperSessionProfileState(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    ARCHIVED = "ARCHIVED"


_ALLOWED_TRANSITIONS: Final = {
    OperationalPaperSessionProfileState.DRAFT: frozenset(
        {
            OperationalPaperSessionProfileState.APPROVED,
            OperationalPaperSessionProfileState.ARCHIVED,
        }
    ),
    OperationalPaperSessionProfileState.APPROVED: frozenset(
        {OperationalPaperSessionProfileState.ARCHIVED}
    ),
    OperationalPaperSessionProfileState.ARCHIVED: frozenset(),
}


def is_operational_paper_session_profile_transition_allowed(
    current: OperationalPaperSessionProfileState,
    target: OperationalPaperSessionProfileState,
) -> bool:
    if not isinstance(current, OperationalPaperSessionProfileState) or not isinstance(
        target, OperationalPaperSessionProfileState
    ):
        return False
    return target in _ALLOWED_TRANSITIONS[current]


def require_operational_paper_session_profile_transition(
    current: OperationalPaperSessionProfileState,
    target: OperationalPaperSessionProfileState,
) -> None:
    if not is_operational_paper_session_profile_transition_allowed(current, target):
        raise OperationalPaperSessionProfileStateTransitionConflictError()


@dataclass(frozen=True, slots=True)
class OperationalPaperSessionProfileMandateBinding:
    mandate_id: UUID
    approved_revision: int
    specification_checksum: str

    def __post_init__(self) -> None:
        try:
            _require_uuid(self.mandate_id)
            _require_positive_int(self.approved_revision)
            _require_sha256(self.specification_checksum)
        except Exception:
            raise InvalidOperationalPaperSessionProfileSpecificationError() from None


@dataclass(frozen=True, slots=True)
class OperationalPaperSessionProfileStrategySnapshot:
    strategy_definition_id: UUID
    source_revision: int
    plugin_name: str
    plugin_version: str
    plugin_schema_version: int
    strategy_lifecycle_version: int
    parameters: StrategyParameters
    parameters_checksum: str
    snapshot_checksum: str
    snapshot_schema_version: int = STRATEGY_SNAPSHOT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        try:
            _validate_strategy_snapshot_fields(self)
            descriptor = StrategyDescriptor(self.plugin_name, self.plugin_version, self.parameters)
            object.__setattr__(self, "plugin_name", descriptor.name)
            object.__setattr__(self, "plugin_version", descriptor.version)
            object.__setattr__(self, "parameters", descriptor.parameters)
            _require_sha256(self.snapshot_checksum)
            if self.snapshot_checksum != _strategy_snapshot_checksum_unchecked(self):
                raise OperationalPaperSessionProfileChecksumMismatchError()
        except OperationalPaperSessionProfileChecksumMismatchError:
            raise
        except Exception:
            raise InvalidOperationalPaperSessionProfileStrategySnapshotError() from None


def build_operational_paper_session_profile_strategy_snapshot(
    *,
    strategy_definition_id: UUID,
    source_revision: int,
    plugin_name: str,
    plugin_version: str,
    plugin_schema_version: int,
    strategy_lifecycle_version: int,
    parameters: StrategyParameters,
    parameters_checksum: str,
    snapshot_schema_version: int = STRATEGY_SNAPSHOT_SCHEMA_VERSION,
) -> OperationalPaperSessionProfileStrategySnapshot:
    try:
        descriptor = StrategyDescriptor(plugin_name, plugin_version, parameters)
        values: dict[str, object] = {
            "strategy_definition_id": strategy_definition_id,
            "source_revision": source_revision,
            "plugin_name": descriptor.name,
            "plugin_version": descriptor.version,
            "plugin_schema_version": plugin_schema_version,
            "strategy_lifecycle_version": strategy_lifecycle_version,
            "parameters": descriptor.parameters,
            "parameters_checksum": parameters_checksum,
            "snapshot_schema_version": snapshot_schema_version,
        }
        provisional = object.__new__(OperationalPaperSessionProfileStrategySnapshot)
        for name, value in values.items():
            object.__setattr__(provisional, name, value)
        object.__setattr__(provisional, "snapshot_checksum", "0" * 64)
        _validate_strategy_snapshot_fields(provisional)
        checksum = _strategy_snapshot_checksum_unchecked(provisional)
        return OperationalPaperSessionProfileStrategySnapshot(
            strategy_definition_id=strategy_definition_id,
            source_revision=source_revision,
            plugin_name=descriptor.name,
            plugin_version=descriptor.version,
            plugin_schema_version=plugin_schema_version,
            strategy_lifecycle_version=strategy_lifecycle_version,
            parameters=descriptor.parameters,
            parameters_checksum=parameters_checksum,
            snapshot_checksum=checksum,
            snapshot_schema_version=snapshot_schema_version,
        )
    except DomainError:
        raise
    except Exception:
        raise InvalidOperationalPaperSessionProfileStrategySnapshotError() from None


def operational_paper_session_profile_strategy_snapshot_payload(
    snapshot: OperationalPaperSessionProfileStrategySnapshot,
) -> dict[str, object]:
    return _strategy_snapshot_payload_unchecked(_revalidate_strategy_snapshot(snapshot))


def operational_paper_session_profile_strategy_snapshot_checksum(
    snapshot: OperationalPaperSessionProfileStrategySnapshot,
) -> str:
    return _strategy_snapshot_checksum_unchecked(_revalidate_strategy_snapshot(snapshot))


@dataclass(frozen=True, slots=True)
class OperationalPaperSessionProfileSpecification:
    schema_version: int
    name: str
    description: str
    mandate_binding: OperationalPaperSessionProfileMandateBinding
    selected_instrument: OperationalMandateInstrument
    timeframe: Timeframe
    start_at: datetime
    warmup_candles: int
    strategy_snapshot: OperationalPaperSessionProfileStrategySnapshot
    execution: ExecutionAssumptions
    instrument_constraints: InstrumentConstraints
    risk_limits: RiskLimits
    history_window: int
    max_candles: int
    max_orders: int
    max_events: int
    engine_version: str
    market_regime_policy: MarketRegimePolicy | None = None

    def __post_init__(self) -> None:
        try:
            if (
                type(self.schema_version) is not int
                or self.schema_version != OPERATIONAL_PAPER_SESSION_PROFILE_SPEC_SCHEMA_VERSION
            ):
                raise ValueError
            values = _validate_profile_inputs(self)
            strategy = _revalidate_strategy_snapshot(self.strategy_snapshot)
            warmup = _bounded_nonnegative_int(
                self.warmup_candles,
                MAX_OPERATIONAL_PAPER_SESSION_PROFILE_WARMUP_CANDLES,
            )
            if warmup > 0 and strategy.strategy_lifecycle_version != 2:
                raise ValueError
        except OperationalPaperSessionProfileBoundsExceededError:
            raise
        except Exception:
            raise InvalidOperationalPaperSessionProfileSpecificationError() from None
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "strategy_snapshot", strategy)


def operational_paper_session_profile_specification_payload(
    specification: OperationalPaperSessionProfileSpecification,
) -> dict[str, object]:
    canonical = _revalidate_specification(specification)
    return _profile_inputs_payload(canonical) | {
        "schema_version": canonical.schema_version,
        "strategy_snapshot": _strategy_snapshot_payload_unchecked(canonical.strategy_snapshot)
        | {"snapshot_checksum": canonical.strategy_snapshot.snapshot_checksum},
    }


def operational_paper_session_profile_specification_bytes(
    specification: OperationalPaperSessionProfileSpecification,
) -> bytes:
    return canonical_json_bytes(
        operational_paper_session_profile_specification_payload(specification)
    )


def operational_paper_session_profile_specification_checksum(
    specification: OperationalPaperSessionProfileSpecification,
) -> str:
    return hashlib.sha256(
        operational_paper_session_profile_specification_bytes(specification)
    ).hexdigest()


def validate_operational_paper_session_profile_specification_checksum(
    specification: OperationalPaperSessionProfileSpecification,
    expected_checksum: object,
) -> OperationalPaperSessionProfileSpecification:
    """Return the canonical specification when stored checksum evidence agrees."""

    canonical = _revalidate_specification(specification)
    try:
        checksum = _require_sha256(expected_checksum)
    except Exception:
        raise InvalidOperationalPaperSessionProfileSpecificationError() from None
    if operational_paper_session_profile_specification_checksum(canonical) != checksum:
        raise OperationalPaperSessionProfileChecksumMismatchError()
    return canonical


def operational_paper_session_profile_specifications_equal(
    left: OperationalPaperSessionProfileSpecification,
    right: OperationalPaperSessionProfileSpecification,
) -> bool:
    return operational_paper_session_profile_specification_bytes(
        left
    ) == operational_paper_session_profile_specification_bytes(right)


@dataclass(frozen=True, slots=True)
class OperationalPaperSessionProfileCreateIntent:
    """Administrator semantics captured before mutable strategy resolution."""

    name: str
    description: str
    mandate_binding: OperationalPaperSessionProfileMandateBinding
    selected_instrument: OperationalMandateInstrument
    timeframe: Timeframe
    start_at: datetime
    warmup_candles: int
    strategy_definition_id: UUID
    expected_strategy_definition_revision: int
    expected_strategy_parameters_checksum: str
    execution: ExecutionAssumptions
    instrument_constraints: InstrumentConstraints
    risk_limits: RiskLimits
    history_window: int
    max_candles: int
    max_orders: int
    max_events: int
    engine_version: str
    market_regime_policy: MarketRegimePolicy | None = None

    def __post_init__(self) -> None:
        try:
            values = _validate_profile_inputs(self)
            strategy_id = _require_uuid(self.strategy_definition_id)
            revision = _require_positive_int(self.expected_strategy_definition_revision)
            checksum = _require_sha256(self.expected_strategy_parameters_checksum)
        except OperationalPaperSessionProfileBoundsExceededError:
            raise
        except Exception:
            raise InvalidOperationalPaperSessionProfileSpecificationError() from None
        for name, value in values.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "strategy_definition_id", strategy_id)
        object.__setattr__(self, "expected_strategy_definition_revision", revision)
        object.__setattr__(self, "expected_strategy_parameters_checksum", checksum)


def operational_paper_session_profile_create_intent_fingerprint(
    intent: OperationalPaperSessionProfileCreateIntent,
) -> str:
    canonical = _revalidate_create_intent(intent)
    payload = _profile_inputs_payload(canonical) | {
        "contract_version": OPERATIONAL_PAPER_SESSION_PROFILE_CREATE_CONTRACT_VERSION,
        "strategy_definition_id": str(canonical.strategy_definition_id),
        "expected_strategy_definition_revision": (canonical.expected_strategy_definition_revision),
        "expected_strategy_parameters_checksum": (canonical.expected_strategy_parameters_checksum),
    }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def validate_operational_paper_session_profile_idempotency_key(value: object) -> str:
    if not isinstance(value, str):
        raise InvalidOperationalPaperSessionProfileSpecificationError()
    if not 1 <= len(value) <= MAX_OPERATIONAL_PAPER_SESSION_PROFILE_IDEMPOTENCY_KEY_LENGTH:
        raise OperationalPaperSessionProfileBoundsExceededError()
    if _SAFE_TOKEN.fullmatch(value) is None:
        raise InvalidOperationalPaperSessionProfileSpecificationError()
    return value


@dataclass(frozen=True, slots=True)
class OperationalPaperSessionProfileRevision:
    profile_id: UUID
    revision: int
    specification: OperationalPaperSessionProfileSpecification
    specification_checksum: str
    created_by: UUID
    created_at: datetime

    def __post_init__(self) -> None:
        try:
            _require_uuid(self.profile_id)
            _require_positive_int(self.revision)
            specification = validate_operational_paper_session_profile_specification_checksum(
                self.specification,
                self.specification_checksum,
            )
            _require_uuid(self.created_by)
            created_at = _require_utc(self.created_at)
        except OperationalPaperSessionProfileChecksumMismatchError:
            raise
        except Exception:
            raise InvalidOperationalPaperSessionProfileSpecificationError() from None
        object.__setattr__(self, "specification", specification)
        object.__setattr__(self, "created_at", created_at)


@dataclass(frozen=True, slots=True)
class OperationalPaperSessionProfile:
    profile_id: UUID
    state: OperationalPaperSessionProfileState
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
    create_intent_fingerprint: str

    def __post_init__(self) -> None:
        try:
            _require_uuid(self.profile_id)
            if not isinstance(self.state, OperationalPaperSessionProfileState):
                raise ValueError
            _require_positive_int(self.current_revision)
            _require_positive_int(self.record_version)
            _require_uuid(self.created_by)
            created_at = _require_utc(self.created_at)
            key = validate_operational_paper_session_profile_idempotency_key(
                self.create_idempotency_key
            )
            fingerprint = _require_sha256(self.create_intent_fingerprint)
            approval = _collective_presence(
                self.approved_revision,
                self.approved_checksum,
                self.approved_by,
                self.approved_at,
            )
            archive = _collective_presence(self.archived_by, self.archived_at)
            approved_at = self._validate_approval(approval, created_at)
            archived_at = self._validate_archive(archive, created_at)
            valid_state = (
                (
                    self.state is OperationalPaperSessionProfileState.DRAFT
                    and not approval
                    and not archive
                )
                or (
                    self.state is OperationalPaperSessionProfileState.APPROVED
                    and approval
                    and not archive
                )
                or (self.state is OperationalPaperSessionProfileState.ARCHIVED and archive)
            )
            if not valid_state or (
                approved_at is not None and archived_at is not None and archived_at < approved_at
            ):
                raise ValueError
        except OperationalPaperSessionProfileBoundsExceededError:
            raise
        except Exception:
            raise InvalidOperationalPaperSessionProfileSpecificationError() from None
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "approved_at", approved_at)
        object.__setattr__(self, "archived_at", archived_at)
        object.__setattr__(self, "create_idempotency_key", key)
        object.__setattr__(self, "create_intent_fingerprint", fingerprint)

    def _validate_approval(self, present: bool, created_at: datetime) -> datetime | None:
        if not present:
            return None
        if (
            self.approved_revision is None
            or self.approved_checksum is None
            or self.approved_by is None
            or self.approved_at is None
        ):
            raise ValueError
        _require_positive_int(self.approved_revision)
        _require_sha256(self.approved_checksum)
        _require_uuid(self.approved_by)
        approved_at = _require_utc(self.approved_at)
        if self.approved_revision != self.current_revision or approved_at < created_at:
            raise ValueError
        return approved_at

    def _validate_archive(self, present: bool, created_at: datetime) -> datetime | None:
        if not present:
            return None
        if self.archived_by is None or self.archived_at is None:
            raise ValueError
        _require_uuid(self.archived_by)
        archived_at = _require_utc(self.archived_at)
        if archived_at < created_at:
            raise ValueError
        return archived_at


def _strategy_snapshot_payload_unchecked(
    snapshot: OperationalPaperSessionProfileStrategySnapshot,
) -> dict[str, object]:
    return {
        "snapshot_schema_version": snapshot.snapshot_schema_version,
        "strategy_definition_id": str(snapshot.strategy_definition_id),
        "source_revision": snapshot.source_revision,
        "plugin_name": snapshot.plugin_name,
        "plugin_version": snapshot.plugin_version,
        "plugin_schema_version": snapshot.plugin_schema_version,
        "strategy_lifecycle_version": snapshot.strategy_lifecycle_version,
        "parameters": [
            {"name": name, **_parameter_payload(value)} for name, value in snapshot.parameters
        ],
        "parameters_checksum": snapshot.parameters_checksum,
    }


def _strategy_snapshot_checksum_unchecked(
    snapshot: OperationalPaperSessionProfileStrategySnapshot,
) -> str:
    return hashlib.sha256(
        canonical_json_bytes(_strategy_snapshot_payload_unchecked(snapshot))
    ).hexdigest()


def _validate_strategy_snapshot_fields(
    snapshot: OperationalPaperSessionProfileStrategySnapshot,
) -> None:
    _require_uuid(snapshot.strategy_definition_id)
    _require_positive_int(snapshot.source_revision)
    _require_safe_token(snapshot.plugin_name)
    _require_safe_token(snapshot.plugin_version)
    _require_positive_int(snapshot.plugin_schema_version)
    if (
        type(snapshot.strategy_lifecycle_version) is not int
        or snapshot.strategy_lifecycle_version not in {1, 2}
        or type(snapshot.snapshot_schema_version) is not int
        or snapshot.snapshot_schema_version != STRATEGY_SNAPSHOT_SCHEMA_VERSION
    ):
        raise ValueError
    _require_sha256(snapshot.parameters_checksum)


def _revalidate_strategy_snapshot(
    value: object,
) -> OperationalPaperSessionProfileStrategySnapshot:
    if not isinstance(value, OperationalPaperSessionProfileStrategySnapshot):
        raise InvalidOperationalPaperSessionProfileStrategySnapshotError()
    return OperationalPaperSessionProfileStrategySnapshot(
        strategy_definition_id=value.strategy_definition_id,
        source_revision=value.source_revision,
        plugin_name=value.plugin_name,
        plugin_version=value.plugin_version,
        plugin_schema_version=value.plugin_schema_version,
        strategy_lifecycle_version=value.strategy_lifecycle_version,
        parameters=value.parameters,
        parameters_checksum=value.parameters_checksum,
        snapshot_checksum=value.snapshot_checksum,
        snapshot_schema_version=value.snapshot_schema_version,
    )


def _parameter_payload(value: object) -> dict[str, object]:
    if value is None:
        return {"type": "null", "value": None}
    if isinstance(value, bool):
        return {"type": "boolean", "value": value}
    if isinstance(value, int):
        return {"type": "integer", "value": value}
    if isinstance(value, Decimal):
        return {"type": "decimal", "value": decimal_text(value)}
    if isinstance(value, str):
        return {"type": "string", "value": value}
    raise InvalidOperationalPaperSessionProfileStrategySnapshotError()


def _validate_profile_inputs(value: object) -> dict[str, object]:
    name = _normalize_human_text(
        getattr(value, "name"),
        maximum=MAX_OPERATIONAL_PAPER_SESSION_PROFILE_NAME_LENGTH,
        allow_empty=False,
    )
    description = _normalize_human_text(
        getattr(value, "description"),
        maximum=MAX_OPERATIONAL_PAPER_SESSION_PROFILE_DESCRIPTION_LENGTH,
        allow_empty=True,
    )
    binding = _revalidate_mandate_binding(getattr(value, "mandate_binding"))
    instrument = require_operational_mandate_capability(getattr(value, "selected_instrument"))
    timeframe = _canonical_timeframe(getattr(value, "timeframe"))
    start_at = _require_utc(getattr(value, "start_at"))
    if not timeframe.validate_open_time(start_at):
        raise ValueError
    warmup = _bounded_nonnegative_int(
        getattr(value, "warmup_candles"),
        MAX_OPERATIONAL_PAPER_SESSION_PROFILE_WARMUP_CANDLES,
    )
    execution = _revalidate_execution(getattr(value, "execution"))
    if execution.force_close_at_end:
        raise ValueError
    constraints = _revalidate_constraints(getattr(value, "instrument_constraints"))
    risk_limits = _revalidate_risk_limits(getattr(value, "risk_limits"))
    history = _bounded_positive_int(
        getattr(value, "history_window"),
        MAX_OPERATIONAL_PAPER_SESSION_PROFILE_HISTORY_WINDOW,
    )
    max_candles = _bounded_positive_int(
        getattr(value, "max_candles"),
        MAX_OPERATIONAL_PAPER_SESSION_PROFILE_CANDLES,
    )
    max_orders = _bounded_positive_int(
        getattr(value, "max_orders"),
        MAX_OPERATIONAL_PAPER_SESSION_PROFILE_ORDERS,
    )
    max_events = _bounded_positive_int(
        getattr(value, "max_events"),
        MAX_OPERATIONAL_PAPER_SESSION_PROFILE_EVENTS,
    )
    if warmup > history or history > max_candles:
        raise ValueError
    return {
        "name": name,
        "description": description,
        "mandate_binding": binding,
        "selected_instrument": instrument,
        "timeframe": timeframe,
        "start_at": start_at,
        "warmup_candles": warmup,
        "execution": execution,
        "instrument_constraints": constraints,
        "risk_limits": risk_limits,
        "history_window": history,
        "max_candles": max_candles,
        "max_orders": max_orders,
        "max_events": max_events,
        "engine_version": _require_safe_token(getattr(value, "engine_version")),
        "market_regime_policy": _revalidate_market_regime_policy(
            getattr(value, "market_regime_policy")
        ),
    }


def _profile_inputs_payload(value: object) -> dict[str, object]:
    binding = getattr(value, "mandate_binding")
    instrument = getattr(value, "selected_instrument")
    timeframe = getattr(value, "timeframe")
    start_at = getattr(value, "start_at")
    return {
        "name": getattr(value, "name"),
        "description": getattr(value, "description"),
        "mandate_binding": {
            "mandate_id": str(binding.mandate_id),
            "approved_revision": binding.approved_revision,
            "specification_checksum": binding.specification_checksum,
        },
        "selected_instrument": {
            "exchange": instrument.exchange.value,
            "market_type": instrument.market_type.value,
            "base": instrument.pair.base,
            "quote": instrument.pair.quote,
        },
        "timeframe": timeframe.code,
        "start_at": start_at.isoformat(),
        "warmup_candles": getattr(value, "warmup_candles"),
        "execution": canonical_value(getattr(value, "execution")),
        "instrument_constraints": canonical_value(getattr(value, "instrument_constraints")),
        "risk_limits": canonical_value(getattr(value, "risk_limits")),
        "history_window": getattr(value, "history_window"),
        "max_candles": getattr(value, "max_candles"),
        "max_orders": getattr(value, "max_orders"),
        "max_events": getattr(value, "max_events"),
        "engine_version": getattr(value, "engine_version"),
        "market_regime_policy": canonical_value(getattr(value, "market_regime_policy")),
    }


def _revalidate_specification(
    value: object,
) -> OperationalPaperSessionProfileSpecification:
    if not isinstance(value, OperationalPaperSessionProfileSpecification):
        raise InvalidOperationalPaperSessionProfileSpecificationError()
    return OperationalPaperSessionProfileSpecification(
        schema_version=value.schema_version,
        name=value.name,
        description=value.description,
        mandate_binding=value.mandate_binding,
        selected_instrument=value.selected_instrument,
        timeframe=value.timeframe,
        start_at=value.start_at,
        warmup_candles=value.warmup_candles,
        strategy_snapshot=value.strategy_snapshot,
        execution=value.execution,
        instrument_constraints=value.instrument_constraints,
        risk_limits=value.risk_limits,
        history_window=value.history_window,
        max_candles=value.max_candles,
        max_orders=value.max_orders,
        max_events=value.max_events,
        engine_version=value.engine_version,
        market_regime_policy=value.market_regime_policy,
    )


def _revalidate_create_intent(
    value: object,
) -> OperationalPaperSessionProfileCreateIntent:
    if not isinstance(value, OperationalPaperSessionProfileCreateIntent):
        raise InvalidOperationalPaperSessionProfileSpecificationError()
    return OperationalPaperSessionProfileCreateIntent(
        name=value.name,
        description=value.description,
        mandate_binding=value.mandate_binding,
        selected_instrument=value.selected_instrument,
        timeframe=value.timeframe,
        start_at=value.start_at,
        warmup_candles=value.warmup_candles,
        strategy_definition_id=value.strategy_definition_id,
        expected_strategy_definition_revision=value.expected_strategy_definition_revision,
        expected_strategy_parameters_checksum=value.expected_strategy_parameters_checksum,
        execution=value.execution,
        instrument_constraints=value.instrument_constraints,
        risk_limits=value.risk_limits,
        history_window=value.history_window,
        max_candles=value.max_candles,
        max_orders=value.max_orders,
        max_events=value.max_events,
        engine_version=value.engine_version,
        market_regime_policy=value.market_regime_policy,
    )


def _revalidate_mandate_binding(
    value: object,
) -> OperationalPaperSessionProfileMandateBinding:
    if not isinstance(value, OperationalPaperSessionProfileMandateBinding):
        raise ValueError
    return OperationalPaperSessionProfileMandateBinding(
        value.mandate_id,
        value.approved_revision,
        value.specification_checksum,
    )


def _canonical_timeframe(value: object) -> Timeframe:
    if not isinstance(value, Timeframe) or not isinstance(value.code, str):
        raise ValueError
    canonical = TIMEFRAMES.get(value.code)
    if canonical is None or value != canonical:
        raise ValueError
    return canonical


def _revalidate_execution(value: object) -> ExecutionAssumptions:
    if not isinstance(value, ExecutionAssumptions) or type(value) not in {
        ExecutionAssumptions,
        PositionSizedExecutionAssumptions,
    }:
        raise ValueError
    if type(value.fees) is not FeeModel or type(value.slippage) is not SlippageModel:
        raise ValueError
    fees = copy(value.fees)
    FeeModel.__post_init__(fees)
    slippage = copy(value.slippage)
    if not isinstance(slippage.kind, SlippageKind):
        raise ValueError
    SlippageModel.__post_init__(slippage)
    if (
        not isinstance(value.intrabar_policy, IntrabarPolicy)
        or type(value.force_close_at_end) is not bool
    ):
        raise ValueError
    if isinstance(value, PositionSizedExecutionAssumptions):
        if type(value.position_sizing) is not PositionSizingPolicy:
            raise ValueError
        policy = copy(value.position_sizing)
        PositionSizingPolicy.__post_init__(policy)
        candidate: ExecutionAssumptions = PositionSizedExecutionAssumptions(
            fees, slippage, value.intrabar_policy, value.force_close_at_end, policy
        )
    else:
        candidate = ExecutionAssumptions(
            fees, slippage, value.intrabar_policy, value.force_close_at_end
        )
    if candidate != value:
        raise ValueError
    return candidate


def _revalidate_constraints(value: object) -> InstrumentConstraints:
    if type(value) is not InstrumentConstraints:
        raise ValueError
    candidate = copy(value)
    InstrumentConstraints.__post_init__(candidate)
    if candidate != value:
        raise ValueError
    return candidate


def _revalidate_risk_limits(value: object) -> RiskLimits:
    if not isinstance(value, RiskLimits) or type(value) not in {RiskLimits, StopLossRiskLimits}:
        raise ValueError
    if (
        type(value.max_open_orders) is not int
        or type(value.max_total_orders) is not int
        or type(value.stop_on_max_drawdown) is not bool
        or type(value.allow_all_in) is not bool
    ):
        raise ValueError
    candidate = copy(value)
    if isinstance(candidate, StopLossRiskLimits):
        if type(candidate.stop_loss) is not StopLossPolicy:
            raise ValueError
        StopLossRiskLimits.__post_init__(candidate)
    else:
        RiskLimits.__post_init__(candidate)
    if candidate != value:
        raise ValueError
    return candidate


def _revalidate_market_regime_policy(value: object) -> MarketRegimePolicy | None:
    if value is None:
        return None
    if type(value) is not MarketRegimePolicy:
        raise ValueError
    candidate = copy(value)
    MarketRegimePolicy.__post_init__(candidate)
    if candidate != value:
        raise ValueError
    return candidate


def _normalize_human_text(value: object, *, maximum: int, allow_empty: bool) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise ValueError
    normalized = unicodedata.normalize("NFC", value)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n").strip()
    if (not allow_empty and not normalized) or len(normalized) > maximum:
        raise OperationalPaperSessionProfileBoundsExceededError()
    return normalized


def _require_safe_token(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError
    normalized = value.strip()
    if normalized != value or _SAFE_TOKEN.fullmatch(normalized) is None:
        raise ValueError
    return normalized


def _require_uuid(value: object) -> UUID:
    if not isinstance(value, UUID) or value.int == 0:
        raise ValueError
    return value


def _require_positive_int(value: object) -> int:
    if type(value) is not int or value < 1:
        raise ValueError
    return value


def _bounded_positive_int(value: object, maximum: int) -> int:
    if type(value) is not int or value < 1:
        raise ValueError
    if value > maximum:
        raise OperationalPaperSessionProfileBoundsExceededError()
    return value


def _bounded_nonnegative_int(value: object, maximum: int) -> int:
    if type(value) is not int or value < 0:
        raise ValueError
    if value > maximum:
        raise OperationalPaperSessionProfileBoundsExceededError()
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
