"""Strict canonical document codec for reproducible experiment manifests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation

from app.backtesting.domain import (
    ExecutionAssumptions,
    FeeModel,
    InstrumentConstraints,
    IntrabarPolicy,
    RiskLimits,
    SlippageKind,
    SlippageModel,
    StrategyDescriptor,
)
from app.optimization.canonical import canonical_json_bytes, decimal_text, document_checksum
from app.optimization.documents import decode_document
from app.optimization.domain import (
    ParameterCombination,
    ParameterSearchSpace,
    validate_parameter_combination,
)
from app.optimization.errors import (
    ExperimentChecksumError,
    IncompatibleExperimentDocumentError,
    IncompatibleSearchSpaceDocumentError,
    ParameterSearchError,
    PlannedRunSpecChecksumError,
    TemporalSegmentationError,
    UnsupportedExperimentSchemaError,
)
from app.optimization.experiment_domain import (
    EXPERIMENT_SCHEMA_VERSION,
    SUPPORTED_EXPERIMENT_SCHEMA_VERSIONS,
    ExperimentBacktestConfiguration,
    ExperimentHoldoutPolicy,
    ExperimentOrderingPolicy,
    ExperimentPlan,
    ExperimentPluginReference,
    ExperimentRunPurpose,
    PlannedRunSpec,
    backtest_configuration_payload,
    combination_payload,
    combination_reference_payload,
    experiment_plan_payload,
    normalized_parameters_from_document,
    parameter_document_payload,
    segment_reference_payload,
    validate_experiment_plan,
)
from app.optimization.temporal_documents import decode_temporal_document
from app.optimization.temporal_domain import TemporalSegmentationPlan, temporal_snapshot_payload
from app.strategies.definitions import (
    StoredStrategyParameter,
    StrategyParameterDocument,
)
from app.strategies.domain import StrategyParameterKind
from app.strategies.errors import InvalidStrategyDefinitionError

ExperimentDocumentEnvelope = dict[str, object]


def experiment_to_document(plan: ExperimentPlan) -> ExperimentDocumentEnvelope:
    """Return a fresh JSON-compatible envelope after structural revalidation."""

    validate_experiment_plan(plan)
    return {
        "experiment_plan": experiment_plan_payload(plan),
        "checksum": plan.checksum,
        "experiment_id": plan.experiment_id,
    }


def canonical_experiment_document_bytes(plan: ExperimentPlan) -> bytes:
    return canonical_json_bytes(experiment_to_document(plan))


def decode_experiment_document(envelope: Mapping[str, object]) -> ExperimentPlan:
    """Strictly reconstruct every nested canonical planning contract."""

    root = _mapping(envelope, "experiment envelope")
    _exact_fields(root, {"experiment_plan", "checksum", "experiment_id"}, "experiment envelope")
    payload = _mapping(root["experiment_plan"], "experiment payload")
    checksum = _text(root["checksum"], "experiment checksum")
    experiment_id = _text(root["experiment_id"], "experiment id")
    if _checksum(payload) != checksum:
        raise ExperimentChecksumError()
    _exact_fields(
        payload,
        {
            "schema_version",
            "snapshot",
            "temporal_plan",
            "parameter_search_space",
            "combinations",
            "plugin",
            "backtest_configuration",
            "holdout_policy",
            "ordering_policy",
            "max_run_specs",
            "cardinality",
            "run_specs",
        },
        "experiment payload",
    )
    schema_version = _integer(payload["schema_version"], "experiment schema version")
    if schema_version not in SUPPORTED_EXPERIMENT_SCHEMA_VERSIONS:
        raise UnsupportedExperimentSchemaError(
            f"unsupported experiment schema version: {schema_version}"
        )
    try:
        temporal_plan = decode_temporal_document(
            _mapping(payload["temporal_plan"], "temporal plan")
        )
    except TemporalSegmentationError as error:
        raise IncompatibleExperimentDocumentError(
            "nested temporal-plan document is incompatible"
        ) from error
    snapshot_value = _mapping(payload["snapshot"], "snapshot")
    if dict(snapshot_value) != temporal_snapshot_payload(temporal_plan.snapshot):
        raise IncompatibleExperimentDocumentError("experiment snapshot diverges from temporal plan")
    try:
        search_space = decode_document(
            _mapping(payload["parameter_search_space"], "parameter search space")
        )
    except ParameterSearchError as error:
        raise IncompatibleExperimentDocumentError(
            "nested parameter-search document is incompatible"
        ) from error
    plugin = _decode_plugin(payload["plugin"])
    configuration = _decode_backtest_configuration(payload["backtest_configuration"])
    combinations = tuple(
        _decode_combination(item, search_space)
        for item in _sequence(payload["combinations"], "combinations")
    )
    try:
        holdout_policy = ExperimentHoldoutPolicy(_text(payload["holdout_policy"], "holdout policy"))
        ordering_policy = ExperimentOrderingPolicy(
            _text(payload["ordering_policy"], "ordering policy")
        )
    except ValueError:
        raise IncompatibleExperimentDocumentError("experiment policy enum is unknown") from None
    run_specs = tuple(
        _decode_run_spec(
            item,
            experiment_id=experiment_id,
            combinations=combinations,
            temporal_plan=temporal_plan,
            plugin=plugin,
            configuration=configuration,
        )
        for item in _sequence(payload["run_specs"], "run specs")
    )
    return ExperimentPlan(
        snapshot=temporal_plan.snapshot,
        temporal_plan=temporal_plan,
        search_space=search_space,
        combinations=combinations,
        plugin=plugin,
        backtest_configuration=configuration,
        run_specs=run_specs,
        cardinality=_integer(payload["cardinality"], "experiment cardinality"),
        max_run_specs=_integer(payload["max_run_specs"], "maximum planned runs"),
        checksum=checksum,
        experiment_id=experiment_id,
        holdout_policy=holdout_policy,
        ordering_policy=ordering_policy,
        schema_version=schema_version,
    )


def _decode_plugin(raw: object) -> ExperimentPluginReference:
    value = _mapping(raw, "plugin")
    _exact_fields(
        value,
        {"name", "version", "schema_version", "lifecycle_version"},
        "plugin",
    )
    return ExperimentPluginReference(
        name=_text(value["name"], "plugin name"),
        version=_text(value["version"], "plugin version"),
        schema_version=_integer(value["schema_version"], "plugin schema version"),
        lifecycle_version=_integer(value["lifecycle_version"], "plugin lifecycle version"),
    )


def _decode_backtest_configuration(raw: object) -> ExperimentBacktestConfiguration:
    value = _mapping(raw, "backtest configuration")
    _exact_fields(
        value,
        {
            "initial_capital",
            "execution",
            "constraints",
            "risk_limits",
            "limits",
            "engine_version",
            "backtest_schema_version",
        },
        "backtest configuration",
    )
    execution = _mapping(value["execution"], "execution")
    _exact_fields(
        execution,
        {"fees", "slippage", "intrabar_policy", "force_close_at_end"},
        "execution",
    )
    fees = _mapping(execution["fees"], "fees")
    _exact_fields(fees, {"maker_fee_bps", "taker_fee_bps"}, "fees")
    slippage = _mapping(execution["slippage"], "slippage")
    _exact_fields(slippage, {"kind", "fixed_bps"}, "slippage")
    constraints = _mapping(value["constraints"], "constraints")
    _exact_fields(
        constraints,
        {
            "minimum_quantity",
            "quantity_step",
            "price_tick",
            "minimum_notional",
            "maximum_notional",
        },
        "constraints",
    )
    risk = _mapping(value["risk_limits"], "risk limits")
    _exact_fields(
        risk,
        {
            "max_order_notional",
            "max_position_notional",
            "max_open_orders",
            "max_total_orders",
            "max_drawdown_pct",
            "stop_on_max_drawdown",
            "allow_all_in",
            "minimum_quote_reserve",
        },
        "risk limits",
    )
    limits = _mapping(value["limits"], "backtest limits")
    _exact_fields(
        limits,
        {"history_window", "max_candles", "max_orders", "max_events"},
        "backtest limits",
    )
    try:
        execution_contract = ExecutionAssumptions(
            fees=FeeModel(
                _decimal(fees["maker_fee_bps"], "maker fee"),
                _decimal(fees["taker_fee_bps"], "taker fee"),
            ),
            slippage=SlippageModel(
                kind=SlippageKind(_text(slippage["kind"], "slippage kind")),
                fixed_bps=_decimal(slippage["fixed_bps"], "fixed slippage"),
            ),
            intrabar_policy=IntrabarPolicy(_text(execution["intrabar_policy"], "intrabar policy")),
            force_close_at_end=_boolean(execution["force_close_at_end"], "force-close policy"),
        )
    except ValueError as error:
        raise IncompatibleExperimentDocumentError(
            "execution model enum or value is invalid"
        ) from error
    configuration = ExperimentBacktestConfiguration(
        initial_capital=_decimal(value["initial_capital"], "initial capital"),
        execution=execution_contract,
        constraints=InstrumentConstraints(
            minimum_quantity=_decimal(constraints["minimum_quantity"], "minimum quantity"),
            quantity_step=_decimal(constraints["quantity_step"], "quantity step"),
            price_tick=_decimal(constraints["price_tick"], "price tick"),
            minimum_notional=_decimal(constraints["minimum_notional"], "minimum notional"),
            maximum_notional=_optional_decimal(constraints["maximum_notional"], "maximum notional"),
        ),
        risk_limits=RiskLimits(
            max_order_notional=_optional_decimal(
                risk["max_order_notional"], "maximum order notional"
            ),
            max_position_notional=_optional_decimal(
                risk["max_position_notional"], "maximum position notional"
            ),
            max_open_orders=_integer(risk["max_open_orders"], "maximum open orders"),
            max_total_orders=_integer(risk["max_total_orders"], "maximum total orders"),
            max_drawdown_pct=_optional_decimal(
                risk["max_drawdown_pct"], "maximum drawdown percentage"
            ),
            stop_on_max_drawdown=_boolean(risk["stop_on_max_drawdown"], "stop-on-drawdown policy"),
            allow_all_in=_boolean(risk["allow_all_in"], "all-in policy"),
            minimum_quote_reserve=_decimal(risk["minimum_quote_reserve"], "minimum quote reserve"),
        ),
        history_window=_integer(limits["history_window"], "history window"),
        max_candles=_integer(limits["max_candles"], "maximum candles"),
        max_orders=_integer(limits["max_orders"], "maximum orders"),
        max_events=_integer(limits["max_events"], "maximum events"),
        engine_version=_text(value["engine_version"], "engine version"),
        schema_version=_integer(value["backtest_schema_version"], "backtest schema version"),
    )
    if backtest_configuration_payload(configuration) != dict(value):
        raise IncompatibleExperimentDocumentError("backtest configuration is not canonical")
    return configuration


def _decode_combination(
    raw: object,
    search_space: ParameterSearchSpace,
) -> ParameterCombination:
    value = _mapping(raw, "combination")
    _exact_fields(
        value,
        {
            "index",
            "combination_id",
            "parameters_checksum",
            "parameters",
            "parameter_document",
        },
        "combination",
    )
    parameters_document = _decode_parameter_document(value["parameters"], "parameters")
    canonical_document = _decode_parameter_document(
        value["parameter_document"], "parameter document"
    )
    if parameters_document != canonical_document:
        raise IncompatibleExperimentDocumentError(
            "normalized parameters diverge from parameter document"
        )
    try:
        combination = ParameterCombination(
            index=_integer(value["index"], "combination index"),
            parameters=normalized_parameters_from_document(canonical_document),
            parameter_document=canonical_document,
            parameters_checksum=_text(value["parameters_checksum"], "parameter checksum"),
            combination_id=_text(value["combination_id"], "combination id"),
        )
        validate_parameter_combination(combination, search_space)
    except ParameterSearchError as error:
        raise IncompatibleExperimentDocumentError(
            "combination is incompatible with parameter search space"
        ) from error
    if combination_payload(combination) != dict(value):
        raise IncompatibleExperimentDocumentError("combination is not canonical")
    return combination


def _decode_run_spec(
    raw: object,
    *,
    experiment_id: str,
    combinations: tuple[ParameterCombination, ...],
    temporal_plan: TemporalSegmentationPlan,
    plugin: ExperimentPluginReference,
    configuration: ExperimentBacktestConfiguration,
) -> PlannedRunSpec:
    envelope = _mapping(raw, "run-spec envelope")
    _exact_fields(envelope, {"run_spec", "checksum", "run_spec_id"}, "run-spec envelope")
    payload = _mapping(envelope["run_spec"], "run-spec payload")
    checksum = _text(envelope["checksum"], "run-spec checksum")
    if _checksum(payload) != checksum:
        raise PlannedRunSpecChecksumError()
    _exact_fields(
        payload,
        {
            "schema_version",
            "global_index",
            "combination_reference",
            "segment_reference",
            "purpose",
            "eligible_for_model_selection",
        },
        "run-spec payload",
    )
    schema_version = _integer(payload["schema_version"], "run-spec schema version")
    if schema_version != EXPERIMENT_SCHEMA_VERSION:
        raise UnsupportedExperimentSchemaError(
            f"unsupported planned-run schema version: {schema_version}"
        )
    combination_value = _mapping(payload["combination_reference"], "planned combination reference")
    _exact_fields(
        combination_value,
        {"index", "combination_id", "parameters_checksum"},
        "planned combination reference",
    )
    combination_index = _integer(combination_value.get("index"), "planned combination index")
    if combination_index < 0 or combination_index >= len(combinations):
        raise IncompatibleExperimentDocumentError("planned combination index is invalid")
    combination = combinations[combination_index]
    if dict(combination_value) != combination_reference_payload(combination):
        raise IncompatibleExperimentDocumentError("planned combination diverges from manifest")
    segment_value = _mapping(payload["segment_reference"], "planned segment reference")
    _exact_fields(
        segment_value,
        {"index", "segment_id", "checksum"},
        "planned segment reference",
    )
    segment_index = _integer(segment_value.get("index"), "planned segment index")
    if segment_index < 0 or segment_index >= len(temporal_plan.segments):
        raise IncompatibleExperimentDocumentError("planned segment index is invalid")
    segment_id = _text(segment_value.get("segment_id"), "planned segment id")
    segment = temporal_plan.segments[segment_index]
    if segment.segment_id != segment_id or dict(segment_value) != segment_reference_payload(
        segment
    ):
        raise IncompatibleExperimentDocumentError("planned segment diverges from temporal plan")
    backtest_config = configuration.for_segment(
        snapshot_id=temporal_plan.snapshot.snapshot_id,
        strategy=StrategyDescriptor(plugin.name, plugin.version, combination.parameters),
        segment=segment,
        strategy_lifecycle_version=plugin.lifecycle_version,
    )
    try:
        purpose = ExperimentRunPurpose(_text(payload["purpose"], "run purpose"))
    except ValueError:
        raise IncompatibleExperimentDocumentError("run purpose enum is unknown") from None
    return PlannedRunSpec(
        experiment_id=experiment_id,
        global_index=_integer(payload["global_index"], "global run index"),
        combination=combination,
        segment=segment,
        snapshot=temporal_plan.snapshot,
        plugin=plugin,
        backtest_config=backtest_config,
        purpose=purpose,
        eligible_for_model_selection=_boolean(
            payload["eligible_for_model_selection"], "selection eligibility"
        ),
        checksum=checksum,
        run_spec_id=_text(envelope["run_spec_id"], "run-spec id"),
        schema_version=schema_version,
    )


def _decode_parameter_document(raw: object, label: str) -> StrategyParameterDocument:
    entries = _sequence(raw, label)
    result: list[StoredStrategyParameter] = []
    for raw_entry in entries:
        entry = _mapping(raw_entry, f"{label} entry")
        _exact_fields(entry, {"name", "kind", "value"}, f"{label} entry")
        try:
            kind = StrategyParameterKind(_text(entry["kind"], "parameter kind"))
        except ValueError:
            raise IncompatibleExperimentDocumentError("parameter kind is unknown") from None
        stored_value = entry["value"]
        if stored_value is not None and not isinstance(stored_value, (bool, int, str)):
            raise IncompatibleExperimentDocumentError("stored parameter value is invalid")
        try:
            result.append(
                StoredStrategyParameter(
                    _text(entry["name"], "parameter name"),
                    kind,
                    stored_value,
                )
            )
        except InvalidStrategyDefinitionError as error:
            raise IncompatibleExperimentDocumentError(
                "stored parameter entry is incompatible"
            ) from error
    document = tuple(result)
    if parameter_document_payload(document) != [dict(_mapping(item, label)) for item in entries]:
        raise IncompatibleExperimentDocumentError(f"{label} is not canonical")
    return document


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise IncompatibleExperimentDocumentError(f"{label} must be an object")
    return value


def _sequence(value: object, label: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise IncompatibleExperimentDocumentError(f"{label} must be an array")
    return value


def _exact_fields(value: Mapping[str, object], expected: set[str], label: str) -> None:
    if set(value) != expected:
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        raise IncompatibleExperimentDocumentError(
            f"{label} fields are incompatible; missing={missing}, extra={extra}"
        )


def _text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise IncompatibleExperimentDocumentError(f"{label} must be a string")
    return value


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise IncompatibleExperimentDocumentError(f"{label} must be an integer")
    return value


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise IncompatibleExperimentDocumentError(f"{label} must be boolean")
    return value


def _decimal(value: object, label: str) -> Decimal:
    text = _text(value, label)
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        raise IncompatibleExperimentDocumentError(f"{label} is not Decimal") from None
    try:
        canonical = decimal_text(parsed)
    except IncompatibleSearchSpaceDocumentError as error:
        raise IncompatibleExperimentDocumentError(f"{label} is invalid") from error
    if canonical != text:
        raise IncompatibleExperimentDocumentError(f"{label} is not canonical Decimal text")
    return parsed


def _optional_decimal(value: object, label: str) -> Decimal | None:
    return None if value is None else _decimal(value, label)


def _checksum(payload: Mapping[str, object]) -> str:
    try:
        return document_checksum(dict(payload))
    except (IncompatibleSearchSpaceDocumentError, ValueError) as error:
        raise IncompatibleExperimentDocumentError(
            "experiment payload is not canonical JSON"
        ) from error


__all__ = [
    "ExperimentDocumentEnvelope",
    "canonical_experiment_document_bytes",
    "decode_experiment_document",
    "experiment_to_document",
]
