"""PostgreSQL persistence for Phase 7-07 operational paper-session profiles."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import NoReturn, cast
from uuid import UUID, uuid4

from psycopg import Error
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from app.backtesting.domain import (
    ExecutionAssumptions,
    FeeModel,
    InstrumentConstraints,
    IntrabarPolicy,
    PositionSizedExecutionAssumptions,
    PositionSizingKind,
    PositionSizingPolicy,
    RiskLimits,
    SlippageKind,
    SlippageModel,
    StopLossKind,
    StopLossPolicy,
    StopLossRiskLimits,
    StrategyParameters,
)
from app.backtesting.serialization import canonical_value, decimal_text
from app.database.errors import raise_domain_error
from app.database.pool import Database, DatabaseConnection
from app.domain.errors import DomainError, PersistenceError
from app.indicators.regime import MarketRegimePolicy
from app.market_data.domain import Exchange, MarketType, Timeframe, TradingPair
from app.market_data.timeframes import TIMEFRAMES
from app.operational_mandates import (
    OperationalMandateInstrument,
    OperationalMandateState,
)
from app.operational_mandates.errors import (
    OperationalMandateChecksumMismatchError,
    OperationalMandateNotFoundError,
    OperationalMandateRevisionConflictError,
    OperationalMandateStateTransitionConflictError,
)
from app.operational_paper_session_profiles import (
    OperationalPaperSessionProfile,
    OperationalPaperSessionProfileCreateIntent,
    OperationalPaperSessionProfileMandateBinding,
    OperationalPaperSessionProfileRevision,
    OperationalPaperSessionProfileSpecification,
    OperationalPaperSessionProfileState,
    OperationalPaperSessionProfileStrategySnapshot,
    build_operational_paper_session_profile_strategy_snapshot,
    operational_paper_session_profile_create_intent_fingerprint,
    operational_paper_session_profile_specification_checksum,
    operational_paper_session_profile_specifications_equal,
    operational_paper_session_profile_strategy_snapshot_payload,
    validate_operational_paper_session_profile_idempotency_key,
)
from app.operational_paper_session_profiles.errors import (
    InvalidOperationalPaperSessionProfileSpecificationError,
    OperationalPaperSessionProfileChecksumMismatchError,
    OperationalPaperSessionProfileIdempotencyConflictError,
    OperationalPaperSessionProfileNotFoundError,
    OperationalPaperSessionProfileRecordVersionConflictError,
    OperationalPaperSessionProfileRevisionConflictError,
    OperationalPaperSessionProfileStateTransitionConflictError,
)
from app.repositories.operational_mandates import operational_mandate_from_row
from app.strategies.definitions import (
    StrategyDefinition,
    StrategyDefinitionSpec,
    StrategyDefinitionState,
    StrategyParameterDocument,
    strategy_parameter_document_from_json,
)
from app.strategies.domain import StrategyParameterKind
from app.strategies.errors import (
    InvalidStrategyDefinitionError,
    StrategyDefinitionArchivedError,
    StrategyDefinitionCompatibilityError,
    StrategyDefinitionNotFoundError,
    StrategyDefinitionRevisionConflictError,
)

StrategyParametersResolver = Callable[[StrategyDefinition], StrategyParameters]

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDEMPOTENCY_CONSTRAINT = "operational_paper_session_profiles_actor_idempotency_key"

_AGGREGATE_COLUMNS = """
    profile_id,
    state,
    current_revision,
    record_version,
    approved_revision,
    approved_checksum,
    created_by,
    created_at,
    approved_by,
    approved_at,
    archived_by,
    archived_at,
    create_idempotency_key,
    create_intent_fingerprint
"""

_REVISION_COLUMNS = """
    profile_id,
    revision,
    schema_version,
    specification_checksum,
    name,
    description,
    mandate_id,
    mandate_approved_revision,
    mandate_specification_checksum,
    exchange,
    market_type,
    base_asset,
    quote_asset,
    timeframe,
    start_at,
    warmup_candles,
    strategy_definition_id,
    strategy_source_revision,
    strategy_plugin_name,
    strategy_plugin_version,
    strategy_plugin_schema_version,
    strategy_lifecycle_version,
    strategy_parameters,
    strategy_parameters_checksum,
    strategy_snapshot_checksum,
    strategy_snapshot_schema_version,
    execution,
    instrument_constraints,
    risk_limits,
    history_window,
    max_candles,
    max_orders,
    max_events,
    engine_version,
    market_regime_policy,
    market_regime_policy is null as market_regime_policy_is_sql_null,
    created_by,
    created_at
"""

_MANDATE_COLUMNS = """
    mandate_id,
    state,
    current_revision,
    record_version,
    approved_revision,
    approved_checksum,
    created_by,
    created_at,
    approved_by,
    approved_at,
    archived_by,
    archived_at,
    create_idempotency_key,
    create_request_fingerprint
"""

_STRATEGY_COLUMNS = """
    id,
    display_name,
    plugin_name,
    plugin_version,
    plugin_schema_version,
    lifecycle_version,
    parameters,
    parameters_checksum,
    state,
    revision,
    created_by,
    updated_by,
    created_at,
    updated_at,
    archived_at
"""

_REVISION_CONFLICT_MESSAGES = frozenset(
    {
        "operational_paper_session_profile_initial_revision_invalid",
        "operational_paper_session_profile_revision_sequence_invalid",
        "operational_paper_session_profile_revision_not_published",
        "operational_paper_session_profile_revision_publication_invalid",
        "operational_paper_session_profile_revision_missing",
    }
)
_STATE_CONFLICT_MESSAGES = frozenset(
    {
        "operational_paper_session_profile_revision_append_forbidden",
        "operational_paper_session_profile_terminal",
        "operational_paper_session_profile_approval_invalid",
        "operational_paper_session_profile_draft_archive_invalid",
        "operational_paper_session_profile_approved_archive_invalid",
        "operational_paper_session_profile_transition_invalid",
    }
)
_CHECKSUM_CONSTRAINTS = frozenset(
    {
        "operational_paper_session_profiles_approved_checksum_check",
        "operational_paper_session_profiles_approved_revision_fkey",
        "operational_paper_session_profile_revisions_checksum_check",
    }
)


def _value(row: Mapping[str, object], key: str) -> object:
    try:
        return row[key]
    except KeyError:
        raise TypeError("persisted row is incomplete") from None


def _text(row: Mapping[str, object], key: str) -> str:
    value = _value(row, key)
    if not isinstance(value, str):
        raise TypeError("persisted value must be text")
    return value


def _optional_text(row: Mapping[str, object], key: str) -> str | None:
    value = _value(row, key)
    if value is not None and not isinstance(value, str):
        raise TypeError("persisted value must be optional text")
    return value


def _integer(row: Mapping[str, object], key: str) -> int:
    value = _value(row, key)
    if type(value) is not int:
        raise TypeError("persisted value must be an exact integer")
    return value


def _boolean(row: Mapping[str, object], key: str) -> bool:
    value = _value(row, key)
    if type(value) is not bool:
        raise TypeError("persisted value must be a boolean")
    return value


def _optional_integer(row: Mapping[str, object], key: str) -> int | None:
    value = _value(row, key)
    if value is not None and type(value) is not int:
        raise TypeError("persisted value must be an optional exact integer")
    return value


def _uuid(row: Mapping[str, object], key: str) -> UUID:
    value = _value(row, key)
    if not isinstance(value, UUID):
        raise TypeError("persisted value must be a UUID")
    return value


def _optional_uuid(row: Mapping[str, object], key: str) -> UUID | None:
    value = _value(row, key)
    if value is not None and not isinstance(value, UUID):
        raise TypeError("persisted value must be an optional UUID")
    return value


def _timestamp(row: Mapping[str, object], key: str) -> datetime:
    value = _value(row, key)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise TypeError("persisted timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _optional_timestamp(row: Mapping[str, object], key: str) -> datetime | None:
    value = _value(row, key)
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise TypeError("persisted timestamp must be optional and timezone-aware")
    return value.astimezone(UTC)


def _object(row: Mapping[str, object], key: str) -> dict[str, object]:
    value = _value(row, key)
    if not isinstance(value, dict) or not all(isinstance(item, str) for item in value):
        raise TypeError("persisted JSON value must be an object")
    return cast(dict[str, object], value)


def operational_paper_session_profile_from_row(
    row: Mapping[str, object],
) -> OperationalPaperSessionProfile:
    """Strictly reconstruct one persisted profile aggregate."""

    try:
        return OperationalPaperSessionProfile(
            profile_id=_uuid(row, "profile_id"),
            state=OperationalPaperSessionProfileState(_text(row, "state")),
            current_revision=_integer(row, "current_revision"),
            record_version=_integer(row, "record_version"),
            approved_revision=_optional_integer(row, "approved_revision"),
            approved_checksum=_optional_text(row, "approved_checksum"),
            created_by=_uuid(row, "created_by"),
            created_at=_timestamp(row, "created_at"),
            approved_by=_optional_uuid(row, "approved_by"),
            approved_at=_optional_timestamp(row, "approved_at"),
            archived_by=_optional_uuid(row, "archived_by"),
            archived_at=_optional_timestamp(row, "archived_at"),
            create_idempotency_key=_text(row, "create_idempotency_key"),
            create_intent_fingerprint=_text(row, "create_intent_fingerprint"),
        )
    except (DomainError, KeyError, TypeError, ValueError) as error:
        raise PersistenceError() from error


def operational_paper_session_profile_revision_from_row(
    row: Mapping[str, object],
) -> OperationalPaperSessionProfileRevision:
    """Strictly reconstruct one complete immutable profile revision."""

    try:
        parameters = _strategy_parameters(_value(row, "strategy_parameters"))
        snapshot = OperationalPaperSessionProfileStrategySnapshot(
            strategy_definition_id=_uuid(row, "strategy_definition_id"),
            source_revision=_integer(row, "strategy_source_revision"),
            plugin_name=_text(row, "strategy_plugin_name"),
            plugin_version=_text(row, "strategy_plugin_version"),
            plugin_schema_version=_integer(row, "strategy_plugin_schema_version"),
            strategy_lifecycle_version=_integer(row, "strategy_lifecycle_version"),
            parameters=parameters,
            parameters_checksum=_text(row, "strategy_parameters_checksum"),
            snapshot_checksum=_text(row, "strategy_snapshot_checksum"),
            snapshot_schema_version=_integer(row, "strategy_snapshot_schema_version"),
        )
        raw_policy = _value(row, "market_regime_policy")
        policy_is_sql_null = _boolean(row, "market_regime_policy_is_sql_null")
        if (raw_policy is None) != policy_is_sql_null:
            raise TypeError("JSON null must not substitute for SQL NULL")
        policy = None if policy_is_sql_null else _regime(_json_object(raw_policy))
        specification = OperationalPaperSessionProfileSpecification(
            schema_version=_integer(row, "schema_version"),
            name=_text(row, "name"),
            description=_text(row, "description"),
            mandate_binding=OperationalPaperSessionProfileMandateBinding(
                mandate_id=_uuid(row, "mandate_id"),
                approved_revision=_integer(row, "mandate_approved_revision"),
                specification_checksum=_text(row, "mandate_specification_checksum"),
            ),
            selected_instrument=OperationalMandateInstrument(
                exchange=Exchange(_text(row, "exchange")),
                market_type=MarketType(_text(row, "market_type")),
                pair=TradingPair(_text(row, "base_asset"), _text(row, "quote_asset")),
            ),
            timeframe=_timeframe(_text(row, "timeframe")),
            start_at=_timestamp(row, "start_at"),
            warmup_candles=_integer(row, "warmup_candles"),
            strategy_snapshot=snapshot,
            execution=_execution(_object(row, "execution")),
            instrument_constraints=_constraints(_object(row, "instrument_constraints")),
            risk_limits=_risk(_object(row, "risk_limits")),
            history_window=_integer(row, "history_window"),
            max_candles=_integer(row, "max_candles"),
            max_orders=_integer(row, "max_orders"),
            max_events=_integer(row, "max_events"),
            engine_version=_text(row, "engine_version"),
            market_regime_policy=policy,
        )
        return OperationalPaperSessionProfileRevision(
            profile_id=_uuid(row, "profile_id"),
            revision=_integer(row, "revision"),
            specification=specification,
            specification_checksum=_text(row, "specification_checksum"),
            created_by=_uuid(row, "created_by"),
            created_at=_timestamp(row, "created_at"),
        )
    except (DomainError, InvalidOperation, KeyError, TypeError, ValueError) as error:
        raise PersistenceError() from error


def _json_object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(item, str) for item in value):
        raise TypeError("persisted JSON value must be an object")
    return cast(dict[str, object], value)


def _keys(payload: Mapping[str, object], expected: frozenset[str]) -> None:
    if frozenset(payload) != expected:
        raise ValueError("persisted JSON object has an invalid shape")


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("persisted JSON value must be text")
    return value


def _int(value: object) -> int:
    if type(value) is not int:
        raise TypeError("persisted JSON value must be an exact integer")
    return value


def _bool(value: object) -> bool:
    if type(value) is not bool:
        raise TypeError("persisted JSON value must be a boolean")
    return value


def _decimal(value: object) -> Decimal:
    raw = _string(value)
    result = Decimal(raw)
    if not result.is_finite() or decimal_text(result) != raw:
        raise ValueError("persisted Decimal is not canonical")
    return result


def _optional_decimal(value: object) -> Decimal | None:
    return None if value is None else _decimal(value)


def _strategy_parameters(value: object) -> StrategyParameters:
    if not isinstance(value, list):
        raise TypeError("strategy parameters must be an array")
    result: list[tuple[str, object]] = []
    for raw_item in value:
        item = _json_object(raw_item)
        _keys(item, frozenset({"name", "type", "value"}))
        name = _string(item["name"])
        kind = _string(item["type"])
        raw_parameter = item["value"]
        parameter: object
        if kind == "null" and raw_parameter is None:
            parameter = None
        elif kind == "boolean":
            parameter = _bool(raw_parameter)
        elif kind == "integer":
            parameter = _int(raw_parameter)
        elif kind == "decimal":
            parameter = _decimal(raw_parameter)
        elif kind == "string":
            parameter = _string(raw_parameter)
        else:
            raise ValueError("strategy parameter type is invalid")
        result.append((name, parameter))
    return cast(StrategyParameters, tuple(result))


def _execution(payload: dict[str, object]) -> ExecutionAssumptions:
    base_keys = frozenset({"fees", "slippage", "intrabar_policy", "force_close_at_end"})
    keys = frozenset(payload)
    if keys not in {base_keys, base_keys | {"position_sizing"}}:
        raise ValueError("execution JSON has an invalid shape")
    fees = _json_object(payload["fees"])
    slippage = _json_object(payload["slippage"])
    _keys(fees, frozenset({"maker_fee_bps", "taker_fee_bps"}))
    _keys(slippage, frozenset({"kind", "fixed_bps"}))
    fee_model = FeeModel(
        _decimal(fees["maker_fee_bps"]),
        _decimal(fees["taker_fee_bps"]),
    )
    slippage_model = SlippageModel(
        SlippageKind(_string(slippage["kind"])),
        _decimal(slippage["fixed_bps"]),
    )
    intrabar_policy = IntrabarPolicy(_string(payload["intrabar_policy"]))
    force_close_at_end = _bool(payload["force_close_at_end"])
    if "position_sizing" not in payload:
        return ExecutionAssumptions(
            fees=fee_model,
            slippage=slippage_model,
            intrabar_policy=intrabar_policy,
            force_close_at_end=force_close_at_end,
        )
    sizing = _json_object(payload["position_sizing"])
    _keys(sizing, frozenset({"kind", "value", "minimum_quote_reserve"}))
    return PositionSizedExecutionAssumptions(
        fees=fee_model,
        slippage=slippage_model,
        intrabar_policy=intrabar_policy,
        force_close_at_end=force_close_at_end,
        position_sizing=PositionSizingPolicy(
            kind=PositionSizingKind(_string(sizing["kind"])),
            value=_optional_decimal(sizing["value"]),
            minimum_quote_reserve=_decimal(sizing["minimum_quote_reserve"]),
        ),
    )


def _constraints(payload: dict[str, object]) -> InstrumentConstraints:
    _keys(
        payload,
        frozenset(
            {
                "minimum_quantity",
                "quantity_step",
                "price_tick",
                "minimum_notional",
                "maximum_notional",
            }
        ),
    )
    return InstrumentConstraints(
        minimum_quantity=_decimal(payload["minimum_quantity"]),
        quantity_step=_decimal(payload["quantity_step"]),
        price_tick=_decimal(payload["price_tick"]),
        minimum_notional=_decimal(payload["minimum_notional"]),
        maximum_notional=_optional_decimal(payload["maximum_notional"]),
    )


def _risk(payload: dict[str, object]) -> RiskLimits:
    base_keys = frozenset(
        {
            "max_order_notional",
            "max_position_notional",
            "max_open_orders",
            "max_total_orders",
            "max_drawdown_pct",
            "stop_on_max_drawdown",
            "allow_all_in",
            "minimum_quote_reserve",
        }
    )
    keys = frozenset(payload)
    if keys not in {base_keys, base_keys | {"stop_loss"}}:
        raise ValueError("risk JSON has an invalid shape")
    max_order_notional = _optional_decimal(payload["max_order_notional"])
    max_position_notional = _optional_decimal(payload["max_position_notional"])
    max_open_orders = _int(payload["max_open_orders"])
    max_total_orders = _int(payload["max_total_orders"])
    max_drawdown_pct = _optional_decimal(payload["max_drawdown_pct"])
    stop_on_max_drawdown = _bool(payload["stop_on_max_drawdown"])
    allow_all_in = _bool(payload["allow_all_in"])
    minimum_quote_reserve = _decimal(payload["minimum_quote_reserve"])
    if "stop_loss" not in payload:
        return RiskLimits(
            max_order_notional=max_order_notional,
            max_position_notional=max_position_notional,
            max_open_orders=max_open_orders,
            max_total_orders=max_total_orders,
            max_drawdown_pct=max_drawdown_pct,
            stop_on_max_drawdown=stop_on_max_drawdown,
            allow_all_in=allow_all_in,
            minimum_quote_reserve=minimum_quote_reserve,
        )
    stop_loss = _json_object(payload["stop_loss"])
    _keys(stop_loss, frozenset({"kind", "value"}))
    return StopLossRiskLimits(
        max_order_notional=max_order_notional,
        max_position_notional=max_position_notional,
        max_open_orders=max_open_orders,
        max_total_orders=max_total_orders,
        max_drawdown_pct=max_drawdown_pct,
        stop_on_max_drawdown=stop_on_max_drawdown,
        allow_all_in=allow_all_in,
        minimum_quote_reserve=minimum_quote_reserve,
        stop_loss=StopLossPolicy(
            kind=StopLossKind(_string(stop_loss["kind"])),
            value=_optional_decimal(stop_loss["value"]),
        ),
    )


def _regime(payload: dict[str, object]) -> MarketRegimePolicy:
    policy = MarketRegimePolicy(
        fast_ema_period=_int(payload["fast_ema_period"]),
        slow_ema_period=_int(payload["slow_ema_period"]),
        atr_period=_int(payload["atr_period"]),
        volatile_atr_ratio=_decimal(payload["volatile_atr_ratio"]),
        trend_strength_threshold=_decimal(payload["trend_strength_threshold"]),
        schema_version=_int(payload["schema_version"]),
    )
    if canonical_value(policy) != payload:
        raise ValueError("market regime JSON is not canonical")
    return policy


def _timeframe(code: str) -> Timeframe:
    timeframe = TIMEFRAMES.get(code)
    if timeframe is None:
        raise ValueError("persisted timeframe is invalid")
    return timeframe


def _canonical_intent(
    value: OperationalPaperSessionProfileCreateIntent,
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


def _require_uuid(value: object) -> UUID:
    if not isinstance(value, UUID) or value.int == 0:
        raise InvalidOperationalPaperSessionProfileSpecificationError()
    return value


def _expected_revision(value: object) -> int:
    if type(value) is not int or value < 1:
        raise OperationalPaperSessionProfileRevisionConflictError()
    return value


def _expected_record_version(value: object) -> int:
    if type(value) is not int or value < 1:
        raise OperationalPaperSessionProfileRecordVersionConflictError()
    return value


def _expected_checksum(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise OperationalPaperSessionProfileChecksumMismatchError()
    return value


def _now(value: object) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
        or value.utcoffset() != timedelta(0)
    ):
        raise InvalidOperationalPaperSessionProfileSpecificationError()
    return value.astimezone(UTC)


def _raise_profile_database_error(error: Error) -> NoReturn:
    message = error.diag.message_primary or ""
    constraint = error.diag.constraint_name or ""
    if constraint == _IDEMPOTENCY_CONSTRAINT:
        raise OperationalPaperSessionProfileIdempotencyConflictError() from error
    if message == "operational_paper_session_profile_record_version_conflict":
        raise OperationalPaperSessionProfileRecordVersionConflictError() from error
    if message in _REVISION_CONFLICT_MESSAGES:
        raise OperationalPaperSessionProfileRevisionConflictError() from error
    if message in _STATE_CONFLICT_MESSAGES:
        raise OperationalPaperSessionProfileStateTransitionConflictError() from error
    if constraint in _CHECKSUM_CONSTRAINTS:
        raise OperationalPaperSessionProfileChecksumMismatchError() from error
    raise_domain_error(error)


async def _aggregate_row(
    connection: DatabaseConnection,
    profile_id: UUID,
    *,
    lock: str | None = None,
) -> Mapping[str, object] | None:
    lock_clause = "" if lock is None else f" for {lock}"
    cursor = await connection.execute(
        f"""
        select {_AGGREGATE_COLUMNS}
        from public.operational_paper_session_profiles
        where profile_id = %s
        {lock_clause}
        """,
        (profile_id,),
    )
    return await cursor.fetchone()


async def _idempotent_row(
    connection: DatabaseConnection,
    *,
    actor_id: UUID,
    idempotency_key: str,
) -> Mapping[str, object] | None:
    cursor = await connection.execute(
        f"""
        select {_AGGREGATE_COLUMNS}
        from public.operational_paper_session_profiles
        where created_by = %s and create_idempotency_key = %s
        """,
        (actor_id, idempotency_key),
    )
    return await cursor.fetchone()


async def _revision(
    connection: DatabaseConnection,
    profile_id: UUID,
    revision: int,
) -> OperationalPaperSessionProfileRevision | None:
    cursor = await connection.execute(
        f"""
        select {_REVISION_COLUMNS}
        from public.operational_paper_session_profile_revisions
        where profile_id = %s and revision = %s
        """,
        (profile_id, revision),
    )
    row = await cursor.fetchone()
    return None if row is None else operational_paper_session_profile_revision_from_row(row)


async def _current_pair(
    connection: DatabaseConnection,
    row: Mapping[str, object],
) -> tuple[OperationalPaperSessionProfile, OperationalPaperSessionProfileRevision]:
    profile = operational_paper_session_profile_from_row(row)
    revision = await _revision(connection, profile.profile_id, profile.current_revision)
    if revision is None or revision.revision != profile.current_revision:
        raise PersistenceError()
    if profile.approved_revision is not None and (
        profile.approved_revision != revision.revision
        or profile.approved_checksum != revision.specification_checksum
    ):
        raise PersistenceError()
    return profile, revision


def _strategy_definition_from_row(row: Mapping[str, object]) -> StrategyDefinition:
    try:
        raw_parameters = _value(row, "parameters")
        if not isinstance(raw_parameters, Mapping):
            raise TypeError("strategy parameters must be an object")
        spec = StrategyDefinitionSpec(
            display_name=_text(row, "display_name"),
            plugin_name=_text(row, "plugin_name"),
            plugin_version=_text(row, "plugin_version"),
            plugin_schema_version=_integer(row, "plugin_schema_version"),
            lifecycle_version=_integer(row, "lifecycle_version"),
            parameters=strategy_parameter_document_from_json(raw_parameters),
            parameters_checksum=_text(row, "parameters_checksum"),
        )
        return StrategyDefinition(
            id=_uuid(row, "id"),
            spec=spec,
            state=StrategyDefinitionState(_text(row, "state")),
            revision=_integer(row, "revision"),
            created_by=_uuid(row, "created_by"),
            updated_by=_uuid(row, "updated_by"),
            created_at=_timestamp(row, "created_at"),
            updated_at=_timestamp(row, "updated_at"),
            archived_at=_optional_timestamp(row, "archived_at"),
        )
    except (InvalidStrategyDefinitionError, KeyError, TypeError, ValueError) as error:
        raise PersistenceError() from error


async def _locked_strategy(
    connection: DatabaseConnection,
    strategy_definition_id: UUID,
) -> StrategyDefinition:
    cursor = await connection.execute(
        f"""
        select {_STRATEGY_COLUMNS}
        from public.strategy_definitions
        where id = %s
        for share
        """,
        (strategy_definition_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise StrategyDefinitionNotFoundError()
    return _strategy_definition_from_row(row)


def _document_parameters(document: StrategyParameterDocument) -> StrategyParameters:
    values: list[tuple[str, object]] = []
    for item in document:
        if item.kind is StrategyParameterKind.DECIMAL:
            if not isinstance(item.value, str):
                raise StrategyDefinitionCompatibilityError()
            try:
                value: object = Decimal(item.value)
            except InvalidOperation:
                raise StrategyDefinitionCompatibilityError() from None
        else:
            value = item.value
        values.append((item.name, value))
    return cast(StrategyParameters, tuple(values))


def _resolved_snapshot(
    definition: StrategyDefinition,
    resolver: StrategyParametersResolver,
) -> OperationalPaperSessionProfileStrategySnapshot:
    if definition.state is StrategyDefinitionState.ARCHIVED:
        raise StrategyDefinitionArchivedError()
    try:
        parameters = resolver(definition)
        expected_parameters = _document_parameters(definition.spec.parameters)
        if parameters != expected_parameters:
            raise StrategyDefinitionCompatibilityError()
        return build_operational_paper_session_profile_strategy_snapshot(
            strategy_definition_id=definition.id,
            source_revision=definition.revision,
            plugin_name=definition.spec.plugin_name,
            plugin_version=definition.spec.plugin_version,
            plugin_schema_version=definition.spec.plugin_schema_version,
            strategy_lifecycle_version=definition.spec.lifecycle_version,
            parameters=parameters,
            parameters_checksum=definition.spec.parameters_checksum,
        )
    except DomainError:
        raise
    except (TypeError, ValueError):
        raise StrategyDefinitionCompatibilityError() from None


async def _validate_strategy_intent(
    connection: DatabaseConnection,
    intent: OperationalPaperSessionProfileCreateIntent,
    resolver: StrategyParametersResolver,
) -> OperationalPaperSessionProfileStrategySnapshot:
    definition = await _locked_strategy(connection, intent.strategy_definition_id)
    if definition.state is StrategyDefinitionState.ARCHIVED:
        raise StrategyDefinitionArchivedError()
    if definition.revision != intent.expected_strategy_definition_revision:
        raise StrategyDefinitionRevisionConflictError()
    if definition.spec.parameters_checksum != intent.expected_strategy_parameters_checksum:
        raise StrategyDefinitionCompatibilityError()
    return _resolved_snapshot(definition, resolver)


async def _validate_mandate(
    connection: DatabaseConnection,
    binding: OperationalPaperSessionProfileMandateBinding,
    instrument: OperationalMandateInstrument,
) -> None:
    cursor = await connection.execute(
        f"""
        select {_MANDATE_COLUMNS}
        from public.operational_mandates
        where mandate_id = %s
        for share
        """,
        (binding.mandate_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise OperationalMandateNotFoundError()
    mandate = operational_mandate_from_row(row)
    if mandate.state is not OperationalMandateState.APPROVED:
        raise OperationalMandateStateTransitionConflictError()
    if (
        mandate.approved_revision != binding.approved_revision
        or mandate.current_revision != binding.approved_revision
    ):
        raise OperationalMandateRevisionConflictError()
    if mandate.approved_checksum != binding.specification_checksum:
        raise OperationalMandateChecksumMismatchError()
    membership = await connection.execute(
        """
        select 1
        from public.operational_mandate_revision_instruments
        where mandate_id = %s
          and revision = %s
          and exchange = %s
          and market_type = %s
          and base_asset = %s
          and quote_asset = %s
        """,
        (
            binding.mandate_id,
            binding.approved_revision,
            instrument.exchange.value,
            instrument.market_type.value,
            instrument.pair.base,
            instrument.pair.quote,
        ),
    )
    if await membership.fetchone() is None:
        raise OperationalMandateRevisionConflictError()


def _specification(
    intent: OperationalPaperSessionProfileCreateIntent,
    snapshot: OperationalPaperSessionProfileStrategySnapshot,
) -> OperationalPaperSessionProfileSpecification:
    return OperationalPaperSessionProfileSpecification(
        schema_version=1,
        name=intent.name,
        description=intent.description,
        mandate_binding=intent.mandate_binding,
        selected_instrument=intent.selected_instrument,
        timeframe=intent.timeframe,
        start_at=intent.start_at,
        warmup_candles=intent.warmup_candles,
        strategy_snapshot=snapshot,
        execution=intent.execution,
        instrument_constraints=intent.instrument_constraints,
        risk_limits=intent.risk_limits,
        history_window=intent.history_window,
        max_candles=intent.max_candles,
        max_orders=intent.max_orders,
        max_events=intent.max_events,
        engine_version=intent.engine_version,
        market_regime_policy=intent.market_regime_policy,
    )


def _intent_matches(
    intent: OperationalPaperSessionProfileCreateIntent,
    specification: OperationalPaperSessionProfileSpecification,
) -> bool:
    return (
        intent.name == specification.name
        and intent.description == specification.description
        and intent.mandate_binding == specification.mandate_binding
        and intent.selected_instrument == specification.selected_instrument
        and intent.timeframe == specification.timeframe
        and intent.start_at == specification.start_at
        and intent.warmup_candles == specification.warmup_candles
        and intent.execution == specification.execution
        and intent.instrument_constraints == specification.instrument_constraints
        and intent.risk_limits == specification.risk_limits
        and intent.history_window == specification.history_window
        and intent.max_candles == specification.max_candles
        and intent.max_orders == specification.max_orders
        and intent.max_events == specification.max_events
        and intent.engine_version == specification.engine_version
        and intent.market_regime_policy == specification.market_regime_policy
        and intent.strategy_definition_id == specification.strategy_snapshot.strategy_definition_id
        and intent.expected_strategy_definition_revision
        == specification.strategy_snapshot.source_revision
        and intent.expected_strategy_parameters_checksum
        == specification.strategy_snapshot.parameters_checksum
    )


async def _insert_revision(
    connection: DatabaseConnection,
    *,
    profile_id: UUID,
    revision: int,
    specification: OperationalPaperSessionProfileSpecification,
    checksum: str,
    actor_id: UUID,
    now: datetime,
) -> None:
    snapshot = specification.strategy_snapshot
    await connection.execute(
        """
        insert into public.operational_paper_session_profile_revisions (
            profile_id, revision, schema_version, specification_checksum,
            name, description, mandate_id, mandate_approved_revision,
            mandate_specification_checksum, exchange, market_type, base_asset,
            quote_asset, timeframe, start_at, warmup_candles,
            strategy_definition_id, strategy_source_revision,
            strategy_plugin_name, strategy_plugin_version,
            strategy_plugin_schema_version, strategy_lifecycle_version,
            strategy_parameters, strategy_parameters_checksum,
            strategy_snapshot_checksum, strategy_snapshot_schema_version,
            execution, instrument_constraints, risk_limits, history_window,
            max_candles, max_orders, max_events, engine_version,
            market_regime_policy, created_by, created_at
        )
        values (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        """,
        (
            profile_id,
            revision,
            specification.schema_version,
            checksum,
            specification.name,
            specification.description,
            specification.mandate_binding.mandate_id,
            specification.mandate_binding.approved_revision,
            specification.mandate_binding.specification_checksum,
            specification.selected_instrument.exchange.value,
            specification.selected_instrument.market_type.value,
            specification.selected_instrument.pair.base,
            specification.selected_instrument.pair.quote,
            specification.timeframe.code,
            specification.start_at,
            specification.warmup_candles,
            snapshot.strategy_definition_id,
            snapshot.source_revision,
            snapshot.plugin_name,
            snapshot.plugin_version,
            snapshot.plugin_schema_version,
            snapshot.strategy_lifecycle_version,
            Jsonb(
                operational_paper_session_profile_strategy_snapshot_payload(snapshot)["parameters"]
            ),
            snapshot.parameters_checksum,
            snapshot.snapshot_checksum,
            snapshot.snapshot_schema_version,
            Jsonb(canonical_value(specification.execution)),
            Jsonb(canonical_value(specification.instrument_constraints)),
            Jsonb(canonical_value(specification.risk_limits)),
            specification.history_window,
            specification.max_candles,
            specification.max_orders,
            specification.max_events,
            specification.engine_version,
            None
            if specification.market_regime_policy is None
            else Jsonb(canonical_value(specification.market_regime_policy)),
            actor_id,
            now,
        ),
    )


async def _cas_conflict(
    connection: DatabaseConnection,
    profile_id: UUID,
    *,
    expected_revision: int,
    expected_record_version: int,
) -> NoReturn:
    row = await _aggregate_row(connection, profile_id)
    if row is None:
        raise OperationalPaperSessionProfileNotFoundError()
    current = operational_paper_session_profile_from_row(row)
    if current.current_revision != expected_revision:
        raise OperationalPaperSessionProfileRevisionConflictError()
    if current.record_version != expected_record_version:
        raise OperationalPaperSessionProfileRecordVersionConflictError()
    raise OperationalPaperSessionProfileStateTransitionConflictError()


class PostgresOperationalPaperSessionProfileRepository:
    """Transactional PostgreSQL adapter for the Gate 2C profile contract."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(self, profile_id: UUID) -> OperationalPaperSessionProfile | None:
        profile_id = _require_uuid(profile_id)
        try:
            async with self._database.transaction() as connection:
                row = await _aggregate_row(connection, profile_id)
        except Error as error:
            _raise_profile_database_error(error)
        return None if row is None else operational_paper_session_profile_from_row(row)

    async def get_revision(
        self,
        profile_id: UUID,
        revision: int,
    ) -> OperationalPaperSessionProfileRevision | None:
        profile_id = _require_uuid(profile_id)
        revision = _expected_revision(revision)
        try:
            async with self._database.transaction() as connection:
                return await _revision(connection, profile_id, revision)
        except Error as error:
            _raise_profile_database_error(error)

    async def get_current(
        self,
        profile_id: UUID,
    ) -> tuple[OperationalPaperSessionProfile, OperationalPaperSessionProfileRevision] | None:
        profile_id = _require_uuid(profile_id)
        try:
            async with self._database.transaction() as connection:
                row = await _aggregate_row(connection, profile_id, lock="share")
                if row is None:
                    return None
                return await _current_pair(connection, row)
        except Error as error:
            _raise_profile_database_error(error)

    async def create(
        self,
        intent: OperationalPaperSessionProfileCreateIntent,
        *,
        actor_id: UUID,
        idempotency_key: str,
        now: datetime,
        strategy_resolver: StrategyParametersResolver,
    ) -> tuple[OperationalPaperSessionProfile, OperationalPaperSessionProfileRevision]:
        intent = _canonical_intent(intent)
        actor_id = _require_uuid(actor_id)
        idempotency_key = validate_operational_paper_session_profile_idempotency_key(
            idempotency_key
        )
        now = _now(now)
        fingerprint = operational_paper_session_profile_create_intent_fingerprint(intent)
        try:
            async with self._database.transaction() as connection:
                existing = await _idempotent_row(
                    connection,
                    actor_id=actor_id,
                    idempotency_key=idempotency_key,
                )
                if existing is not None:
                    return await self._replay_row(connection, existing, fingerprint)
                await _validate_mandate(
                    connection,
                    intent.mandate_binding,
                    intent.selected_instrument,
                )
                snapshot = await _validate_strategy_intent(
                    connection,
                    intent,
                    strategy_resolver,
                )
                specification = _specification(intent, snapshot)
                checksum = operational_paper_session_profile_specification_checksum(specification)
                profile_id = uuid4()
                await _insert_revision(
                    connection,
                    profile_id=profile_id,
                    revision=1,
                    specification=specification,
                    checksum=checksum,
                    actor_id=actor_id,
                    now=now,
                )
                cursor = await connection.execute(
                    f"""
                    insert into public.operational_paper_session_profiles (
                        profile_id, state, current_revision, record_version,
                        created_by, created_at, create_idempotency_key,
                        create_intent_fingerprint
                    )
                    values (%s, 'DRAFT', 1, 1, %s, %s, %s, %s)
                    returning {_AGGREGATE_COLUMNS}
                    """,
                    (profile_id, actor_id, now, idempotency_key, fingerprint),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise PersistenceError()
                return await _current_pair(connection, row)
        except UniqueViolation as error:
            if error.diag.constraint_name != _IDEMPOTENCY_CONSTRAINT:
                _raise_profile_database_error(error)
            return await self._resolve_replay(
                actor_id=actor_id,
                idempotency_key=idempotency_key,
                fingerprint=fingerprint,
            )
        except Error as error:
            _raise_profile_database_error(error)

    async def replace_draft(
        self,
        profile_id: UUID,
        intent: OperationalPaperSessionProfileCreateIntent,
        *,
        expected_revision: int,
        expected_record_version: int,
        actor_id: UUID,
        now: datetime,
        strategy_resolver: StrategyParametersResolver,
    ) -> tuple[OperationalPaperSessionProfile, OperationalPaperSessionProfileRevision]:
        profile_id = _require_uuid(profile_id)
        intent = _canonical_intent(intent)
        expected_revision = _expected_revision(expected_revision)
        expected_record_version = _expected_record_version(expected_record_version)
        actor_id = _require_uuid(actor_id)
        now = _now(now)
        try:
            async with self._database.transaction() as connection:
                row = await _aggregate_row(connection, profile_id, lock="update")
                if row is None:
                    raise OperationalPaperSessionProfileNotFoundError()
                current = operational_paper_session_profile_from_row(row)
                if current.current_revision != expected_revision:
                    raise OperationalPaperSessionProfileRevisionConflictError()
                if current.record_version != expected_record_version:
                    raise OperationalPaperSessionProfileRecordVersionConflictError()
                if current.state is not OperationalPaperSessionProfileState.DRAFT:
                    raise OperationalPaperSessionProfileStateTransitionConflictError()
                current, revision = await _current_pair(connection, row)
                if _intent_matches(intent, revision.specification):
                    return current, revision
                if now < current.created_at or now < revision.created_at:
                    raise OperationalPaperSessionProfileStateTransitionConflictError()
                await _validate_mandate(
                    connection,
                    intent.mandate_binding,
                    intent.selected_instrument,
                )
                snapshot = await _validate_strategy_intent(
                    connection,
                    intent,
                    strategy_resolver,
                )
                specification = _specification(intent, snapshot)
                if operational_paper_session_profile_specifications_equal(
                    revision.specification,
                    specification,
                ):
                    return current, revision
                new_revision = current.current_revision + 1
                checksum = operational_paper_session_profile_specification_checksum(specification)
                await _insert_revision(
                    connection,
                    profile_id=profile_id,
                    revision=new_revision,
                    specification=specification,
                    checksum=checksum,
                    actor_id=actor_id,
                    now=now,
                )
                cursor = await connection.execute(
                    f"""
                    update public.operational_paper_session_profiles
                    set current_revision = %s, record_version = record_version + 1
                    where profile_id = %s and state = 'DRAFT'
                      and current_revision = %s and record_version = %s
                    returning {_AGGREGATE_COLUMNS}
                    """,
                    (
                        new_revision,
                        profile_id,
                        expected_revision,
                        expected_record_version,
                    ),
                )
                updated = await cursor.fetchone()
                if updated is None:
                    await _cas_conflict(
                        connection,
                        profile_id,
                        expected_revision=expected_revision,
                        expected_record_version=expected_record_version,
                    )
                if updated is None:
                    raise PersistenceError()
                return await _current_pair(connection, updated)
        except Error as error:
            _raise_profile_database_error(error)

    async def approve(
        self,
        profile_id: UUID,
        *,
        expected_revision: int,
        expected_checksum: str,
        expected_record_version: int,
        actor_id: UUID,
        now: datetime,
        strategy_resolver: StrategyParametersResolver,
    ) -> OperationalPaperSessionProfile:
        profile_id = _require_uuid(profile_id)
        expected_revision = _expected_revision(expected_revision)
        expected_checksum = _expected_checksum(expected_checksum)
        expected_record_version = _expected_record_version(expected_record_version)
        actor_id = _require_uuid(actor_id)
        now = _now(now)
        try:
            async with self._database.transaction() as connection:
                row = await _aggregate_row(connection, profile_id, lock="update")
                if row is None:
                    raise OperationalPaperSessionProfileNotFoundError()
                current = operational_paper_session_profile_from_row(row)
                if current.state is OperationalPaperSessionProfileState.APPROVED:
                    if (
                        current.approved_revision == expected_revision
                        and current.approved_checksum == expected_checksum
                        and current.approved_by == actor_id
                        and current.record_version == expected_record_version + 1
                    ):
                        return current
                    raise OperationalPaperSessionProfileStateTransitionConflictError()
                if current.state is not OperationalPaperSessionProfileState.DRAFT:
                    raise OperationalPaperSessionProfileStateTransitionConflictError()
                if current.current_revision != expected_revision:
                    raise OperationalPaperSessionProfileRevisionConflictError()
                if current.record_version != expected_record_version:
                    raise OperationalPaperSessionProfileRecordVersionConflictError()
                revision = await _revision(connection, profile_id, current.current_revision)
                if revision is None:
                    raise PersistenceError()
                if revision.specification_checksum != expected_checksum:
                    raise OperationalPaperSessionProfileChecksumMismatchError()
                specification = revision.specification
                await _validate_mandate(
                    connection,
                    specification.mandate_binding,
                    specification.selected_instrument,
                )
                snapshot = specification.strategy_snapshot
                definition = await _locked_strategy(
                    connection,
                    snapshot.strategy_definition_id,
                )
                if definition.state is StrategyDefinitionState.ARCHIVED:
                    raise StrategyDefinitionArchivedError()
                if definition.revision != snapshot.source_revision:
                    raise StrategyDefinitionRevisionConflictError()
                if (
                    definition.spec.plugin_name != snapshot.plugin_name
                    or definition.spec.plugin_version != snapshot.plugin_version
                    or definition.spec.plugin_schema_version != snapshot.plugin_schema_version
                    or definition.spec.lifecycle_version != snapshot.strategy_lifecycle_version
                    or definition.spec.parameters_checksum != snapshot.parameters_checksum
                ):
                    raise StrategyDefinitionCompatibilityError()
                rebuilt = _resolved_snapshot(definition, strategy_resolver)
                if rebuilt != snapshot:
                    raise StrategyDefinitionCompatibilityError()
                if now < current.created_at or now < revision.created_at:
                    raise OperationalPaperSessionProfileStateTransitionConflictError()
                cursor = await connection.execute(
                    f"""
                    update public.operational_paper_session_profiles
                    set state = 'APPROVED', record_version = record_version + 1,
                        approved_revision = current_revision,
                        approved_checksum = %s, approved_by = %s, approved_at = %s
                    where profile_id = %s and state = 'DRAFT'
                      and current_revision = %s and record_version = %s
                    returning {_AGGREGATE_COLUMNS}
                    """,
                    (
                        expected_checksum,
                        actor_id,
                        now,
                        profile_id,
                        expected_revision,
                        expected_record_version,
                    ),
                )
                updated = await cursor.fetchone()
                if updated is None:
                    await _cas_conflict(
                        connection,
                        profile_id,
                        expected_revision=expected_revision,
                        expected_record_version=expected_record_version,
                    )
                if updated is None:
                    raise PersistenceError()
                return operational_paper_session_profile_from_row(updated)
        except Error as error:
            _raise_profile_database_error(error)

    async def archive(
        self,
        profile_id: UUID,
        *,
        expected_record_version: int,
        actor_id: UUID,
        now: datetime,
    ) -> OperationalPaperSessionProfile:
        profile_id = _require_uuid(profile_id)
        expected_record_version = _expected_record_version(expected_record_version)
        actor_id = _require_uuid(actor_id)
        now = _now(now)
        try:
            async with self._database.transaction() as connection:
                row = await _aggregate_row(connection, profile_id, lock="update")
                if row is None:
                    raise OperationalPaperSessionProfileNotFoundError()
                current = operational_paper_session_profile_from_row(row)
                if current.state is OperationalPaperSessionProfileState.ARCHIVED:
                    if (
                        current.archived_by == actor_id
                        and current.record_version == expected_record_version + 1
                    ):
                        return current
                    raise OperationalPaperSessionProfileStateTransitionConflictError()
                if current.record_version != expected_record_version:
                    raise OperationalPaperSessionProfileRecordVersionConflictError()
                if current.state not in {
                    OperationalPaperSessionProfileState.DRAFT,
                    OperationalPaperSessionProfileState.APPROVED,
                }:
                    raise OperationalPaperSessionProfileStateTransitionConflictError()
                if now < current.created_at or (
                    current.approved_at is not None and now < current.approved_at
                ):
                    raise OperationalPaperSessionProfileStateTransitionConflictError()
                cursor = await connection.execute(
                    f"""
                    update public.operational_paper_session_profiles
                    set state = 'ARCHIVED', record_version = record_version + 1,
                        archived_by = %s, archived_at = %s
                    where profile_id = %s and state = %s
                      and current_revision = %s and record_version = %s
                    returning {_AGGREGATE_COLUMNS}
                    """,
                    (
                        actor_id,
                        now,
                        profile_id,
                        current.state.value,
                        current.current_revision,
                        expected_record_version,
                    ),
                )
                updated = await cursor.fetchone()
                if updated is None:
                    raise PersistenceError()
                return operational_paper_session_profile_from_row(updated)
        except Error as error:
            _raise_profile_database_error(error)

    async def _resolve_replay(
        self,
        *,
        actor_id: UUID,
        idempotency_key: str,
        fingerprint: str,
    ) -> tuple[OperationalPaperSessionProfile, OperationalPaperSessionProfileRevision]:
        try:
            async with self._database.transaction() as connection:
                row = await _idempotent_row(
                    connection,
                    actor_id=actor_id,
                    idempotency_key=idempotency_key,
                )
                if row is None:
                    raise PersistenceError()
                return await self._replay_row(connection, row, fingerprint)
        except Error as error:
            _raise_profile_database_error(error)

    async def _replay_row(
        self,
        connection: DatabaseConnection,
        row: Mapping[str, object],
        fingerprint: str,
    ) -> tuple[OperationalPaperSessionProfile, OperationalPaperSessionProfileRevision]:
        profile = operational_paper_session_profile_from_row(row)
        if profile.create_intent_fingerprint != fingerprint:
            raise OperationalPaperSessionProfileIdempotencyConflictError()
        return await _current_pair(connection, row)
