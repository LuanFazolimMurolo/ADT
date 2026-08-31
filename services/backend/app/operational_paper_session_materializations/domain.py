"""Pure operational paper-session materialization domain contracts."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final
from uuid import UUID

from app.backtesting.domain import StrategyDescriptor
from app.backtesting.serialization import canonical_json_bytes
from app.operational_paper_capital_authorizations import (
    OperationalPaperCapitalAuthorizationChecksumMismatchError,
    OperationalPaperCapitalAuthorizationSpecification,
    validate_operational_paper_capital_authorization_specification_checksum,
)
from app.operational_paper_session_materializations.errors import (
    InvalidOperationalPaperSessionMaterializationSpecificationError,
    OperationalPaperSessionMaterializationBoundsExceededError,
    OperationalPaperSessionMaterializationChecksumMismatchError,
    OperationalPaperSessionMaterializationConfigIdentityConflictError,
    OperationalPaperSessionMaterializationProfileBindingConflictError,
    OperationalPaperSessionMaterializationQuoteAssetConflictError,
    OperationalPaperSessionMaterializationStateTransitionConflictError,
)
from app.operational_paper_session_profiles import (
    OperationalPaperSessionProfileChecksumMismatchError,
    OperationalPaperSessionProfileRevision,
)
from app.paper_trading.domain import (
    PaperSessionConfig,
    paper_config_checksum,
    paper_session_id,
)

OPERATIONAL_PAPER_SESSION_MATERIALIZATION_SCHEMA_VERSION: Final = 1
OPERATIONAL_PAPER_SESSION_MATERIALIZATION_CONTRACT_VERSION: Final = 1

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_POSTGRESQL_BIGINT_MAX: Final = (1 << 63) - 1


class OperationalPaperSessionMaterializationState(StrEnum):
    PREPARED = "PREPARED"
    MATERIALIZED = "MATERIALIZED"


_ALLOWED_TRANSITIONS: Final = {
    OperationalPaperSessionMaterializationState.PREPARED: frozenset(
        {OperationalPaperSessionMaterializationState.MATERIALIZED}
    ),
    OperationalPaperSessionMaterializationState.MATERIALIZED: frozenset(),
}


def is_operational_paper_session_materialization_transition_allowed(
    current: OperationalPaperSessionMaterializationState,
    target: OperationalPaperSessionMaterializationState,
) -> bool:
    if not isinstance(current, OperationalPaperSessionMaterializationState):
        return False
    if not isinstance(target, OperationalPaperSessionMaterializationState):
        return False
    return target in _ALLOWED_TRANSITIONS[current]


def require_operational_paper_session_materialization_transition(
    current: OperationalPaperSessionMaterializationState,
    target: OperationalPaperSessionMaterializationState,
) -> None:
    if not is_operational_paper_session_materialization_transition_allowed(current, target):
        raise OperationalPaperSessionMaterializationStateTransitionConflictError()


@dataclass(frozen=True, slots=True)
class OperationalPaperSessionMaterializationAuthorizationBinding:
    authorization_id: UUID
    authorization_checksum: str

    def __post_init__(self) -> None:
        try:
            authorization_id = _require_uuid(self.authorization_id)
            authorization_checksum = _require_sha256(self.authorization_checksum)
        except Exception:
            raise InvalidOperationalPaperSessionMaterializationSpecificationError() from None
        object.__setattr__(self, "authorization_id", authorization_id)
        object.__setattr__(self, "authorization_checksum", authorization_checksum)


@dataclass(frozen=True, slots=True)
class OperationalPaperSessionMaterializationProfileBinding:
    profile_id: UUID
    approved_revision: int
    specification_checksum: str

    def __post_init__(self) -> None:
        try:
            profile_id = _require_uuid(self.profile_id)
            approved_revision = _require_positive_bigint(self.approved_revision)
            specification_checksum = _require_sha256(self.specification_checksum)
        except OperationalPaperSessionMaterializationBoundsExceededError:
            raise
        except Exception:
            raise InvalidOperationalPaperSessionMaterializationSpecificationError() from None
        object.__setattr__(self, "profile_id", profile_id)
        object.__setattr__(self, "approved_revision", approved_revision)
        object.__setattr__(self, "specification_checksum", specification_checksum)


@dataclass(frozen=True, slots=True)
class OperationalPaperSessionMaterializationMandateBinding:
    mandate_id: UUID
    approved_revision: int
    specification_checksum: str

    def __post_init__(self) -> None:
        try:
            mandate_id = _require_uuid(self.mandate_id)
            approved_revision = _require_positive_bigint(self.approved_revision)
            specification_checksum = _require_sha256(self.specification_checksum)
        except OperationalPaperSessionMaterializationBoundsExceededError:
            raise
        except Exception:
            raise InvalidOperationalPaperSessionMaterializationSpecificationError() from None
        object.__setattr__(self, "mandate_id", mandate_id)
        object.__setattr__(self, "approved_revision", approved_revision)
        object.__setattr__(self, "specification_checksum", specification_checksum)


def _require_uuid(value: object) -> UUID:
    if not isinstance(value, UUID) or value.int == 0:
        raise ValueError
    return value


def _require_positive_bigint(value: object) -> int:
    if type(value) is not int or value < 1:
        raise ValueError
    if value > _POSTGRESQL_BIGINT_MAX:
        raise OperationalPaperSessionMaterializationBoundsExceededError()
    return value


def _require_sha256(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError
    return value


@dataclass(frozen=True, slots=True)
class OperationalPaperSessionMaterializationSpecification:
    schema_version: int
    materialization_contract_version: int
    authorization_binding: OperationalPaperSessionMaterializationAuthorizationBinding
    profile_binding: OperationalPaperSessionMaterializationProfileBinding
    mandate_binding: OperationalPaperSessionMaterializationMandateBinding
    simulation_id: UUID
    config_checksum: str
    session_id: str

    def __post_init__(self) -> None:
        try:
            if (
                type(self.schema_version) is not int
                or self.schema_version != OPERATIONAL_PAPER_SESSION_MATERIALIZATION_SCHEMA_VERSION
            ):
                raise ValueError
            if (
                type(self.materialization_contract_version) is not int
                or self.materialization_contract_version
                != OPERATIONAL_PAPER_SESSION_MATERIALIZATION_CONTRACT_VERSION
            ):
                raise ValueError
            authorization_binding = _revalidate_authorization_binding(self.authorization_binding)
            profile_binding = _revalidate_profile_binding(self.profile_binding)
            mandate_binding = _revalidate_mandate_binding(self.mandate_binding)
            simulation_id = _require_uuid(self.simulation_id)
            config_checksum = _require_sha256(self.config_checksum)
            session_id = _require_sha256(self.session_id)
        except OperationalPaperSessionMaterializationBoundsExceededError:
            raise
        except Exception:
            raise InvalidOperationalPaperSessionMaterializationSpecificationError() from None
        object.__setattr__(self, "authorization_binding", authorization_binding)
        object.__setattr__(self, "profile_binding", profile_binding)
        object.__setattr__(self, "mandate_binding", mandate_binding)
        object.__setattr__(self, "simulation_id", simulation_id)
        object.__setattr__(self, "config_checksum", config_checksum)
        object.__setattr__(self, "session_id", session_id)


def _revalidate_authorization_binding(
    value: object,
) -> OperationalPaperSessionMaterializationAuthorizationBinding:
    if not isinstance(value, OperationalPaperSessionMaterializationAuthorizationBinding):
        raise ValueError
    return OperationalPaperSessionMaterializationAuthorizationBinding(
        authorization_id=value.authorization_id,
        authorization_checksum=value.authorization_checksum,
    )


def _revalidate_profile_binding(
    value: object,
) -> OperationalPaperSessionMaterializationProfileBinding:
    if not isinstance(value, OperationalPaperSessionMaterializationProfileBinding):
        raise ValueError
    return OperationalPaperSessionMaterializationProfileBinding(
        profile_id=value.profile_id,
        approved_revision=value.approved_revision,
        specification_checksum=value.specification_checksum,
    )


def _revalidate_mandate_binding(
    value: object,
) -> OperationalPaperSessionMaterializationMandateBinding:
    if not isinstance(value, OperationalPaperSessionMaterializationMandateBinding):
        raise ValueError
    return OperationalPaperSessionMaterializationMandateBinding(
        mandate_id=value.mandate_id,
        approved_revision=value.approved_revision,
        specification_checksum=value.specification_checksum,
    )


def _revalidate_specification(
    value: object,
) -> OperationalPaperSessionMaterializationSpecification:
    if not isinstance(value, OperationalPaperSessionMaterializationSpecification):
        raise InvalidOperationalPaperSessionMaterializationSpecificationError()
    return OperationalPaperSessionMaterializationSpecification(
        schema_version=value.schema_version,
        materialization_contract_version=value.materialization_contract_version,
        authorization_binding=value.authorization_binding,
        profile_binding=value.profile_binding,
        mandate_binding=value.mandate_binding,
        simulation_id=value.simulation_id,
        config_checksum=value.config_checksum,
        session_id=value.session_id,
    )


def operational_paper_session_materialization_specification_payload(
    specification: OperationalPaperSessionMaterializationSpecification,
) -> dict[str, object]:
    canonical = _revalidate_specification(specification)
    return {
        "schema_version": canonical.schema_version,
        "materialization_contract_version": canonical.materialization_contract_version,
        "authorization_id": str(canonical.authorization_binding.authorization_id),
        "authorization_checksum": canonical.authorization_binding.authorization_checksum,
        "profile_id": str(canonical.profile_binding.profile_id),
        "profile_approved_revision": canonical.profile_binding.approved_revision,
        "profile_specification_checksum": canonical.profile_binding.specification_checksum,
        "mandate_id": str(canonical.mandate_binding.mandate_id),
        "mandate_approved_revision": canonical.mandate_binding.approved_revision,
        "mandate_specification_checksum": canonical.mandate_binding.specification_checksum,
        "simulation_id": str(canonical.simulation_id),
        "config_checksum": canonical.config_checksum,
        "session_id": canonical.session_id,
    }


def operational_paper_session_materialization_specification_bytes(
    specification: OperationalPaperSessionMaterializationSpecification,
) -> bytes:
    return canonical_json_bytes(
        operational_paper_session_materialization_specification_payload(specification)
    )


def operational_paper_session_materialization_specification_checksum(
    specification: OperationalPaperSessionMaterializationSpecification,
) -> str:
    return hashlib.sha256(
        operational_paper_session_materialization_specification_bytes(specification)
    ).hexdigest()


def validate_operational_paper_session_materialization_specification_checksum(
    specification: OperationalPaperSessionMaterializationSpecification,
    expected_checksum: object,
) -> OperationalPaperSessionMaterializationSpecification:
    canonical = _revalidate_specification(specification)
    try:
        checksum = _require_sha256(expected_checksum)
    except Exception:
        raise InvalidOperationalPaperSessionMaterializationSpecificationError() from None
    if operational_paper_session_materialization_specification_checksum(canonical) != checksum:
        raise OperationalPaperSessionMaterializationChecksumMismatchError()
    return canonical


def operational_paper_session_materialization_specifications_equal(
    left: OperationalPaperSessionMaterializationSpecification,
    right: OperationalPaperSessionMaterializationSpecification,
) -> bool:
    return operational_paper_session_materialization_specification_bytes(
        left
    ) == operational_paper_session_materialization_specification_bytes(right)


@dataclass(frozen=True, slots=True)
class OperationalPaperSessionMaterializationPlan:
    specification: OperationalPaperSessionMaterializationSpecification
    config: PaperSessionConfig

    def __post_init__(self) -> None:
        try:
            specification = _revalidate_specification(self.specification)
            if not isinstance(self.config, PaperSessionConfig):
                raise ValueError
            config_checksum = paper_config_checksum(self.config)
            session_id = paper_session_id(self.config)
        except OperationalPaperSessionMaterializationConfigIdentityConflictError:
            raise
        except Exception:
            raise InvalidOperationalPaperSessionMaterializationSpecificationError() from None
        if (
            specification.config_checksum != config_checksum
            or specification.session_id != session_id
        ):
            raise OperationalPaperSessionMaterializationConfigIdentityConflictError()
        object.__setattr__(self, "specification", specification)


def _revalidate_profile_revision(
    value: object,
) -> OperationalPaperSessionProfileRevision:
    if not isinstance(value, OperationalPaperSessionProfileRevision):
        raise ValueError
    return OperationalPaperSessionProfileRevision(
        profile_id=value.profile_id,
        revision=value.revision,
        specification=value.specification,
        specification_checksum=value.specification_checksum,
        created_by=value.created_by,
        created_at=value.created_at,
    )


def build_operational_paper_session_materialization_plan(
    *,
    authorization_id: UUID,
    authorization_specification: OperationalPaperCapitalAuthorizationSpecification,
    authorization_checksum: str,
    profile_revision: OperationalPaperSessionProfileRevision,
) -> OperationalPaperSessionMaterializationPlan:
    try:
        authorization_id = _require_uuid(authorization_id)
        authorization_checksum = _require_sha256(authorization_checksum)
        authorization = validate_operational_paper_capital_authorization_specification_checksum(
            authorization_specification,
            authorization_checksum,
        )
        profile = _revalidate_profile_revision(profile_revision)
    except (
        OperationalPaperCapitalAuthorizationChecksumMismatchError,
        OperationalPaperSessionProfileChecksumMismatchError,
    ):
        raise OperationalPaperSessionMaterializationChecksumMismatchError() from None
    except OperationalPaperSessionMaterializationBoundsExceededError:
        raise
    except Exception:
        raise InvalidOperationalPaperSessionMaterializationSpecificationError() from None

    authorization_profile = authorization.profile_binding
    if (
        authorization_profile.profile_id != profile.profile_id
        or authorization_profile.approved_revision != profile.revision
        or authorization_profile.specification_checksum != profile.specification_checksum
    ):
        raise OperationalPaperSessionMaterializationProfileBindingConflictError()

    profile_specification = profile.specification
    selected_instrument = profile_specification.selected_instrument
    if authorization.quote_asset != selected_instrument.pair.quote:
        raise OperationalPaperSessionMaterializationQuoteAssetConflictError()

    try:
        snapshot = profile_specification.strategy_snapshot
        strategy = StrategyDescriptor(
            snapshot.plugin_name,
            snapshot.plugin_version,
            snapshot.parameters,
        )
        config_schema_version = 2 if profile_specification.market_regime_policy is not None else 1
        config = PaperSessionConfig(
            pair=selected_instrument.pair,
            timeframe=profile_specification.timeframe,
            start_at=profile_specification.start_at,
            warmup_candles=profile_specification.warmup_candles,
            strategy=strategy,
            strategy_lifecycle_version=snapshot.strategy_lifecycle_version,
            initial_capital=authorization.authorized_capital,
            execution=profile_specification.execution,
            constraints=profile_specification.instrument_constraints,
            risk_limits=profile_specification.risk_limits,
            history_window=profile_specification.history_window,
            max_candles=profile_specification.max_candles,
            max_orders=profile_specification.max_orders,
            max_events=profile_specification.max_events,
            engine_version=profile_specification.engine_version,
            market_regime_policy=profile_specification.market_regime_policy,
            schema_version=config_schema_version,
        )
    except Exception:
        raise InvalidOperationalPaperSessionMaterializationSpecificationError() from None

    config_checksum = paper_config_checksum(config)
    session_id = paper_session_id(config)

    specification = OperationalPaperSessionMaterializationSpecification(
        schema_version=OPERATIONAL_PAPER_SESSION_MATERIALIZATION_SCHEMA_VERSION,
        materialization_contract_version=OPERATIONAL_PAPER_SESSION_MATERIALIZATION_CONTRACT_VERSION,
        authorization_binding=OperationalPaperSessionMaterializationAuthorizationBinding(
            authorization_id=authorization_id,
            authorization_checksum=authorization_checksum,
        ),
        profile_binding=OperationalPaperSessionMaterializationProfileBinding(
            profile_id=profile.profile_id,
            approved_revision=profile.revision,
            specification_checksum=profile.specification_checksum,
        ),
        mandate_binding=OperationalPaperSessionMaterializationMandateBinding(
            mandate_id=profile_specification.mandate_binding.mandate_id,
            approved_revision=profile_specification.mandate_binding.approved_revision,
            specification_checksum=profile_specification.mandate_binding.specification_checksum,
        ),
        simulation_id=authorization.simulation_id,
        config_checksum=config_checksum,
        session_id=session_id,
    )
    return OperationalPaperSessionMaterializationPlan(
        specification=specification,
        config=config,
    )


@dataclass(frozen=True, slots=True)
class OperationalPaperSessionMaterialization:
    materialization_id: UUID
    schema_version: int
    materialization_contract_version: int
    state: OperationalPaperSessionMaterializationState
    record_version: int
    authorization_binding: OperationalPaperSessionMaterializationAuthorizationBinding
    profile_binding: OperationalPaperSessionMaterializationProfileBinding
    mandate_binding: OperationalPaperSessionMaterializationMandateBinding
    simulation_id: UUID
    config_checksum: str
    session_id: str
    materialization_checksum: str
    prepared_by: UUID
    prepared_at: datetime
    materialized_by: UUID | None
    materialized_at: datetime | None

    def __post_init__(self) -> None:
        try:
            materialization_id = _require_uuid(self.materialization_id)
            if (
                type(self.schema_version) is not int
                or self.schema_version != OPERATIONAL_PAPER_SESSION_MATERIALIZATION_SCHEMA_VERSION
            ):
                raise ValueError
            if (
                type(self.materialization_contract_version) is not int
                or self.materialization_contract_version
                != OPERATIONAL_PAPER_SESSION_MATERIALIZATION_CONTRACT_VERSION
            ):
                raise ValueError
            if not isinstance(self.state, OperationalPaperSessionMaterializationState):
                raise ValueError
            record_version = _require_positive_bigint(self.record_version)
            specification = OperationalPaperSessionMaterializationSpecification(
                schema_version=self.schema_version,
                materialization_contract_version=self.materialization_contract_version,
                authorization_binding=self.authorization_binding,
                profile_binding=self.profile_binding,
                mandate_binding=self.mandate_binding,
                simulation_id=self.simulation_id,
                config_checksum=self.config_checksum,
                session_id=self.session_id,
            )
            specification = (
                validate_operational_paper_session_materialization_specification_checksum(
                    specification,
                    self.materialization_checksum,
                )
            )
            materialization_checksum = _require_sha256(self.materialization_checksum)
            prepared_by = _require_uuid(self.prepared_by)
            prepared_at = _require_utc(self.prepared_at)
            materialized = _collective_presence(
                self.materialized_by,
                self.materialized_at,
            )
            materialized_by: UUID | None = None
            materialized_at: datetime | None = None
            if materialized:
                if self.materialized_by is None or self.materialized_at is None:
                    raise ValueError
                materialized_by = _require_uuid(self.materialized_by)
                materialized_at = _require_utc(self.materialized_at)
                if materialized_at < prepared_at:
                    raise ValueError
            valid_state = (
                self.state is OperationalPaperSessionMaterializationState.PREPARED
                and not materialized
            ) or (
                self.state is OperationalPaperSessionMaterializationState.MATERIALIZED
                and materialized
            )
            if not valid_state:
                raise ValueError
        except OperationalPaperSessionMaterializationBoundsExceededError:
            raise
        except OperationalPaperSessionMaterializationChecksumMismatchError:
            raise
        except Exception:
            raise InvalidOperationalPaperSessionMaterializationSpecificationError() from None
        object.__setattr__(self, "materialization_id", materialization_id)
        object.__setattr__(self, "record_version", record_version)
        object.__setattr__(self, "authorization_binding", specification.authorization_binding)
        object.__setattr__(self, "profile_binding", specification.profile_binding)
        object.__setattr__(self, "mandate_binding", specification.mandate_binding)
        object.__setattr__(self, "simulation_id", specification.simulation_id)
        object.__setattr__(self, "config_checksum", specification.config_checksum)
        object.__setattr__(self, "session_id", specification.session_id)
        object.__setattr__(self, "materialization_checksum", materialization_checksum)
        object.__setattr__(self, "prepared_by", prepared_by)
        object.__setattr__(self, "prepared_at", prepared_at)
        object.__setattr__(self, "materialized_by", materialized_by)
        object.__setattr__(self, "materialized_at", materialized_at)


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


def _revalidate_materialization(
    value: object,
) -> OperationalPaperSessionMaterialization:
    if not isinstance(value, OperationalPaperSessionMaterialization):
        raise InvalidOperationalPaperSessionMaterializationSpecificationError()
    return OperationalPaperSessionMaterialization(
        materialization_id=value.materialization_id,
        schema_version=value.schema_version,
        materialization_contract_version=value.materialization_contract_version,
        state=value.state,
        record_version=value.record_version,
        authorization_binding=value.authorization_binding,
        profile_binding=value.profile_binding,
        mandate_binding=value.mandate_binding,
        simulation_id=value.simulation_id,
        config_checksum=value.config_checksum,
        session_id=value.session_id,
        materialization_checksum=value.materialization_checksum,
        prepared_by=value.prepared_by,
        prepared_at=value.prepared_at,
        materialized_by=value.materialized_by,
        materialized_at=value.materialized_at,
    )


def prepare_operational_paper_session_materialization(
    *,
    materialization_id: UUID,
    plan: OperationalPaperSessionMaterializationPlan,
    prepared_by: UUID,
    prepared_at: datetime,
) -> OperationalPaperSessionMaterialization:
    try:
        materialization_id = _require_uuid(materialization_id)
        if not isinstance(plan, OperationalPaperSessionMaterializationPlan):
            raise ValueError
        plan = OperationalPaperSessionMaterializationPlan(
            specification=plan.specification,
            config=plan.config,
        )
        prepared_by = _require_uuid(prepared_by)
        prepared_at = _require_utc(prepared_at)
    except OperationalPaperSessionMaterializationConfigIdentityConflictError:
        raise
    except Exception:
        raise InvalidOperationalPaperSessionMaterializationSpecificationError() from None

    specification = plan.specification
    materialization_checksum = operational_paper_session_materialization_specification_checksum(
        specification
    )
    return OperationalPaperSessionMaterialization(
        materialization_id=materialization_id,
        schema_version=specification.schema_version,
        materialization_contract_version=specification.materialization_contract_version,
        state=OperationalPaperSessionMaterializationState.PREPARED,
        record_version=1,
        authorization_binding=specification.authorization_binding,
        profile_binding=specification.profile_binding,
        mandate_binding=specification.mandate_binding,
        simulation_id=specification.simulation_id,
        config_checksum=specification.config_checksum,
        session_id=specification.session_id,
        materialization_checksum=materialization_checksum,
        prepared_by=prepared_by,
        prepared_at=prepared_at,
        materialized_by=None,
        materialized_at=None,
    )


def materialize_operational_paper_session_materialization(
    materialization: OperationalPaperSessionMaterialization,
    *,
    materialized_by: UUID,
    materialized_at: datetime,
) -> OperationalPaperSessionMaterialization:
    canonical = _revalidate_materialization(materialization)
    require_operational_paper_session_materialization_transition(
        canonical.state,
        OperationalPaperSessionMaterializationState.MATERIALIZED,
    )
    try:
        actor = _require_uuid(materialized_by)
        occurred_at = _require_utc(materialized_at)
    except Exception:
        raise InvalidOperationalPaperSessionMaterializationSpecificationError() from None
    return OperationalPaperSessionMaterialization(
        materialization_id=canonical.materialization_id,
        schema_version=canonical.schema_version,
        materialization_contract_version=canonical.materialization_contract_version,
        state=OperationalPaperSessionMaterializationState.MATERIALIZED,
        record_version=canonical.record_version + 1,
        authorization_binding=canonical.authorization_binding,
        profile_binding=canonical.profile_binding,
        mandate_binding=canonical.mandate_binding,
        simulation_id=canonical.simulation_id,
        config_checksum=canonical.config_checksum,
        session_id=canonical.session_id,
        materialization_checksum=canonical.materialization_checksum,
        prepared_by=canonical.prepared_by,
        prepared_at=canonical.prepared_at,
        materialized_by=actor,
        materialized_at=occurred_at,
    )
