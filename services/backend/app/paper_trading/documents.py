"""Strict canonical JSON codecs for paper-trading sessions."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal

from app.backtesting.domain import (
    ExecutionAssumptions,
    FeeModel,
    Fill,
    FillLiquidity,
    FillReason,
    InstrumentConstraints,
    IntrabarPolicy,
    OrderIntent,
    OrderSide,
    OrderStatus,
    OrderType,
    PortfolioSnapshot,
    RiskLimits,
    SimulatedOrder,
    SlippageKind,
    SlippageModel,
    StrategyDescriptor,
    StrategyParameterValue,
    TimeInForce,
)
from app.backtesting.serialization import canonical_json_bytes, canonical_value, decimal_text
from app.market_data.domain import DataRange, TradingPair, require_utc
from app.market_data.timeframes import get_timeframe
from app.paper_trading.domain import (
    MAX_PAPER_DOCUMENT_BYTES,
    PaperSessionConfig,
    PaperSessionState,
    PaperSessionStateSummary,
    paper_config_checksum,
    paper_config_payload,
    paper_session_id,
    validate_paper_session_state,
    validate_paper_state_summary,
)
from app.paper_trading.errors import InvalidPaperSessionError, PaperSessionCorruptError

_CONFIG_KEYS = frozenset({"config", "checksum", "session_id"})
_STATE_KEYS = frozenset({"state", "checksum"})
_SUMMARY_KEYS = frozenset({"summary", "checksum"})
_SUMMARY_PAYLOAD_KEYS = frozenset(
    {
        "session_id",
        "config_checksum",
        "state_id",
        "state_checksum",
        "evaluation_end",
        "last_candle_open_time",
        "candles_processed",
        "orders_count",
        "fills_count",
        "portfolio",
        "risk_halt",
        "replayed_at",
        "schema_version",
    }
)
_PAIR_KEYS = frozenset({"base", "quote"})
_EXECUTION_KEYS = frozenset({"fees", "slippage", "intrabar_policy", "force_close_at_end"})
_FEE_KEYS = frozenset({"maker_fee_bps", "taker_fee_bps"})
_SLIPPAGE_KEYS = frozenset({"kind", "fixed_bps"})
_CONSTRAINT_KEYS = frozenset(
    {
        "minimum_quantity",
        "quantity_step",
        "price_tick",
        "minimum_notional",
        "maximum_notional",
    }
)
_RISK_KEYS = frozenset(
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
_RANGE_KEYS = frozenset({"start", "end"})
_INTENT_KEYS = frozenset(
    {
        "side",
        "order_type",
        "quantity",
        "time_in_force",
        "limit_price",
        "stop_price",
        "client_tag",
    }
)
_ORDER_KEYS = frozenset(
    {
        "order_id",
        "created_sequence",
        "created_at",
        "created_candle_index",
        "eligible_candle_index",
        "intent",
        "status",
        "opened_at",
        "terminal_at",
        "rejection_code",
    }
)
_FILL_KEYS = frozenset(
    {
        "fill_id",
        "order_id",
        "reason",
        "liquidity",
        "side",
        "quantity",
        "base_price",
        "execution_price",
        "notional",
        "fee",
        "slippage_cost",
        "event_time",
        "candle_index",
    }
)
_PORTFOLIO_KEYS = frozenset(
    {
        "quote_cash",
        "base_quantity",
        "average_entry_price",
        "realized_pnl",
        "unrealized_pnl",
        "total_fees",
        "total_slippage_cost",
        "equity",
        "peak_equity",
        "drawdown",
        "cost_basis",
        "drawdown_pct",
    }
)


def encode_paper_config(config: PaperSessionConfig) -> bytes:
    payload = paper_config_payload(config)
    encoded = canonical_json_bytes(
        {
            "config": payload,
            "checksum": paper_config_checksum(config),
            "session_id": paper_session_id(config),
        }
    )
    if len(encoded) > MAX_PAPER_DOCUMENT_BYTES:
        raise InvalidPaperSessionError("O documento da sessão excede o limite seguro.")
    return encoded


def decode_paper_config(raw: bytes) -> PaperSessionConfig:
    try:
        document = _load_json(raw)
        if not isinstance(document, dict) or frozenset(document) != _CONFIG_KEYS:
            raise ValueError
        payload = document["config"]
        if not isinstance(payload, dict):
            raise ValueError
        config = _config_from_payload(payload)
        if document["checksum"] != paper_config_checksum(config):
            raise ValueError
        if document["session_id"] != paper_session_id(config):
            raise ValueError
        return config
    except InvalidPaperSessionError:
        raise
    except Exception:
        raise InvalidPaperSessionError("O documento da sessão é inválido.") from None


def encode_paper_state(state: PaperSessionState) -> bytes:
    validate_paper_session_state(state)
    encoded = canonical_json_bytes({"state": canonical_value(state), "checksum": state.checksum})
    if len(encoded) > MAX_PAPER_DOCUMENT_BYTES:
        raise PaperSessionCorruptError()
    return encoded


def decode_paper_state(raw: bytes) -> PaperSessionState:
    try:
        document = _load_json(raw)
        if not isinstance(document, dict) or frozenset(document) != _STATE_KEYS:
            raise ValueError
        payload = document["state"]
        if not isinstance(payload, dict) or document["checksum"] != payload.get("checksum"):
            raise ValueError
        return _state_from_payload(payload)
    except PaperSessionCorruptError:
        raise
    except Exception:
        raise PaperSessionCorruptError() from None


def encode_paper_state_summary(summary: PaperSessionStateSummary) -> bytes:
    validate_paper_state_summary(summary)
    payload = canonical_value(summary)
    checksum = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    encoded = canonical_json_bytes({"summary": payload, "checksum": checksum})
    if len(encoded) > MAX_PAPER_DOCUMENT_BYTES:
        raise PaperSessionCorruptError()
    return encoded


def decode_paper_state_summary(raw: bytes) -> PaperSessionStateSummary:
    try:
        document = _load_json(raw)
        if not isinstance(document, dict) or frozenset(document) != _SUMMARY_KEYS:
            raise ValueError
        payload = _object(document["summary"])
        _require_keys(payload, _SUMMARY_PAYLOAD_KEYS)
        if document["checksum"] != hashlib.sha256(canonical_json_bytes(payload)).hexdigest():
            raise ValueError
        summary = PaperSessionStateSummary(
            session_id=_string(payload["session_id"]),
            config_checksum=_string(payload["config_checksum"]),
            state_id=_string(payload["state_id"]),
            state_checksum=_string(payload["state_checksum"]),
            evaluation_end=_datetime(payload["evaluation_end"]),
            last_candle_open_time=_datetime(payload["last_candle_open_time"]),
            candles_processed=_int(payload["candles_processed"]),
            orders_count=_int(payload["orders_count"]),
            fills_count=_int(payload["fills_count"]),
            portfolio=_portfolio(_object(payload["portfolio"])),
            risk_halt=_bool(payload["risk_halt"]),
            replayed_at=_datetime(payload["replayed_at"]),
            schema_version=_int(payload["schema_version"]),
        )
        if canonical_value(summary) != payload:
            raise ValueError
        return summary
    except PaperSessionCorruptError:
        raise
    except Exception:
        raise PaperSessionCorruptError() from None


def _load_json(raw: bytes) -> object:
    if not isinstance(raw, bytes) or len(raw) > MAX_PAPER_DOCUMENT_BYTES:
        raise ValueError

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError
            value[key] = item
        return value

    return json.loads(raw, object_pairs_hook=reject_duplicates)


def _config_from_payload(payload: dict[str, object]) -> PaperSessionConfig:
    expected = {
        "pair",
        "timeframe",
        "start_at",
        "warmup_candles",
        "strategy",
        "strategy_lifecycle_version",
        "initial_capital",
        "execution",
        "constraints",
        "risk_limits",
        "history_window",
        "max_candles",
        "max_orders",
        "max_events",
        "engine_version",
        "schema_version",
    }
    if set(payload) != expected:
        raise InvalidPaperSessionError()
    pair_payload = _object(payload["pair"])
    _require_keys(pair_payload, _PAIR_KEYS)
    return PaperSessionConfig(
        pair=TradingPair(_string(pair_payload["base"]), _string(pair_payload["quote"])),
        timeframe=get_timeframe(_string(payload["timeframe"])),
        start_at=_datetime(payload["start_at"]),
        warmup_candles=_int(payload["warmup_candles"]),
        strategy=_strategy(_object(payload["strategy"])),
        strategy_lifecycle_version=_int(payload["strategy_lifecycle_version"]),
        initial_capital=_decimal(payload["initial_capital"]),
        execution=_execution(_object(payload["execution"])),
        constraints=_constraints(_object(payload["constraints"])),
        risk_limits=_risk(_object(payload["risk_limits"])),
        history_window=_int(payload["history_window"]),
        max_candles=_int(payload["max_candles"]),
        max_orders=_int(payload["max_orders"]),
        max_events=_int(payload["max_events"]),
        engine_version=_string(payload["engine_version"]),
        schema_version=_int(payload["schema_version"]),
    )


def _state_from_payload(payload: dict[str, object]) -> PaperSessionState:
    expected = {
        "session_id",
        "config_checksum",
        "dataset_version",
        "source_checksum",
        "data_range",
        "evaluation_range",
        "candles_processed",
        "last_candle_open_time",
        "orders",
        "fills",
        "portfolio",
        "risk_halt",
        "replayed_at",
        "state_id",
        "checksum",
        "schema_version",
    }
    if set(payload) != expected:
        raise PaperSessionCorruptError()
    return PaperSessionState(
        session_id=_string(payload["session_id"]),
        config_checksum=_string(payload["config_checksum"]),
        dataset_version=_string(payload["dataset_version"]),
        source_checksum=_string(payload["source_checksum"]),
        data_range=_range(_object(payload["data_range"])),
        evaluation_range=_range(_object(payload["evaluation_range"])),
        candles_processed=_int(payload["candles_processed"]),
        last_candle_open_time=_datetime(payload["last_candle_open_time"]),
        orders=tuple(_order(_object(item)) for item in _list(payload["orders"])),
        fills=tuple(_fill(_object(item)) for item in _list(payload["fills"])),
        portfolio=_portfolio(_object(payload["portfolio"])),
        risk_halt=_bool(payload["risk_halt"]),
        replayed_at=_datetime(payload["replayed_at"]),
        state_id=_string(payload["state_id"]),
        checksum=_string(payload["checksum"]),
        schema_version=_int(payload["schema_version"]),
    )


def _strategy(payload: dict[str, object]) -> StrategyDescriptor:
    if set(payload) != {"name", "version", "parameters"}:
        raise InvalidPaperSessionError()
    parameters = []
    for item in _list(payload["parameters"]):
        parameter = _object(item)
        if set(parameter) != {"name", "type", "value"}:
            raise InvalidPaperSessionError()
        parameters.append(
            (
                _string(parameter["name"]),
                _parameter_value(_string(parameter["type"]), parameter["value"]),
            )
        )
    return StrategyDescriptor(
        name=_string(payload["name"]),
        version=_string(payload["version"]),
        parameters=tuple(parameters),
    )


def _parameter_value(kind: str, value: object) -> StrategyParameterValue:
    if kind == "null" and value is None:
        return None
    if kind == "boolean":
        return _bool(value)
    if kind == "integer":
        return _int(value)
    if kind == "decimal":
        return _decimal(value)
    if kind == "string":
        return _string(value)
    raise InvalidPaperSessionError()


def _execution(payload: dict[str, object]) -> ExecutionAssumptions:
    _require_keys(payload, _EXECUTION_KEYS)
    fees = _object(payload["fees"])
    slippage = _object(payload["slippage"])
    _require_keys(fees, _FEE_KEYS)
    _require_keys(slippage, _SLIPPAGE_KEYS)
    return ExecutionAssumptions(
        fees=FeeModel(
            _decimal(fees["maker_fee_bps"]),
            _decimal(fees["taker_fee_bps"]),
        ),
        slippage=SlippageModel(
            kind=SlippageKind(_string(slippage["kind"])),
            fixed_bps=_decimal(slippage["fixed_bps"]),
        ),
        intrabar_policy=IntrabarPolicy(_string(payload["intrabar_policy"])),
        force_close_at_end=_bool(payload["force_close_at_end"]),
    )


def _constraints(payload: dict[str, object]) -> InstrumentConstraints:
    _require_keys(payload, _CONSTRAINT_KEYS)
    return InstrumentConstraints(
        minimum_quantity=_decimal(payload["minimum_quantity"]),
        quantity_step=_decimal(payload["quantity_step"]),
        price_tick=_decimal(payload["price_tick"]),
        minimum_notional=_decimal(payload["minimum_notional"]),
        maximum_notional=_optional_decimal(payload["maximum_notional"]),
    )


def _risk(payload: dict[str, object]) -> RiskLimits:
    _require_keys(payload, _RISK_KEYS)
    return RiskLimits(
        max_order_notional=_optional_decimal(payload["max_order_notional"]),
        max_position_notional=_optional_decimal(payload["max_position_notional"]),
        max_open_orders=_int(payload["max_open_orders"]),
        max_total_orders=_int(payload["max_total_orders"]),
        max_drawdown_pct=_optional_decimal(payload["max_drawdown_pct"]),
        stop_on_max_drawdown=_bool(payload["stop_on_max_drawdown"]),
        allow_all_in=_bool(payload["allow_all_in"]),
        minimum_quote_reserve=_decimal(payload["minimum_quote_reserve"]),
    )


def _range(payload: dict[str, object]) -> DataRange:
    _require_keys(payload, _RANGE_KEYS)
    return DataRange(_datetime(payload["start"]), _datetime(payload["end"]))


def _intent(payload: dict[str, object]) -> OrderIntent:
    _require_keys(payload, _INTENT_KEYS)
    return OrderIntent(
        side=OrderSide(_string(payload["side"])),
        order_type=OrderType(_string(payload["order_type"])),
        quantity=_decimal(payload["quantity"]),
        time_in_force=TimeInForce(_string(payload["time_in_force"])),
        limit_price=_optional_decimal(payload["limit_price"]),
        stop_price=_optional_decimal(payload["stop_price"]),
        client_tag=(None if payload["client_tag"] is None else _string(payload["client_tag"])),
    )


def _order(payload: dict[str, object]) -> SimulatedOrder:
    _require_keys(payload, _ORDER_KEYS)
    return SimulatedOrder(
        order_id=_string(payload["order_id"]),
        created_sequence=_int(payload["created_sequence"]),
        created_at=_datetime(payload["created_at"]),
        created_candle_index=_int(payload["created_candle_index"]),
        eligible_candle_index=_int(payload["eligible_candle_index"]),
        intent=_intent(_object(payload["intent"])),
        status=OrderStatus(_string(payload["status"])),
        opened_at=_optional_datetime(payload["opened_at"]),
        terminal_at=_optional_datetime(payload["terminal_at"]),
        rejection_code=(
            None if payload["rejection_code"] is None else _string(payload["rejection_code"])
        ),
    )


def _fill(payload: dict[str, object]) -> Fill:
    _require_keys(payload, _FILL_KEYS)
    return Fill(
        fill_id=_string(payload["fill_id"]),
        order_id=_string(payload["order_id"]),
        reason=FillReason(_string(payload["reason"])),
        liquidity=FillLiquidity(_string(payload["liquidity"])),
        side=OrderSide(_string(payload["side"])),
        quantity=_decimal(payload["quantity"]),
        base_price=_decimal(payload["base_price"]),
        execution_price=_decimal(payload["execution_price"]),
        notional=_decimal(payload["notional"]),
        fee=_decimal(payload["fee"]),
        slippage_cost=_decimal(payload["slippage_cost"]),
        event_time=_datetime(payload["event_time"]),
        candle_index=_int(payload["candle_index"]),
    )


def _portfolio(payload: dict[str, object]) -> PortfolioSnapshot:
    _require_keys(payload, _PORTFOLIO_KEYS)
    return PortfolioSnapshot(
        quote_cash=_decimal(payload["quote_cash"]),
        base_quantity=_decimal(payload["base_quantity"]),
        average_entry_price=_decimal(payload["average_entry_price"]),
        realized_pnl=_decimal(payload["realized_pnl"]),
        unrealized_pnl=_decimal(payload["unrealized_pnl"]),
        total_fees=_decimal(payload["total_fees"]),
        total_slippage_cost=_decimal(payload["total_slippage_cost"]),
        equity=_decimal(payload["equity"]),
        peak_equity=_decimal(payload["peak_equity"]),
        drawdown=_decimal(payload["drawdown"]),
        cost_basis=_decimal(payload["cost_basis"]),
        drawdown_pct=_decimal(payload["drawdown_pct"]),
    )


def _require_keys(payload: dict[str, object], expected: frozenset[str]) -> None:
    if frozenset(payload) != expected:
        raise ValueError


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError
    return value


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError
    return value


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError
    return value


def _int(value: object) -> int:
    if type(value) is not int:
        raise ValueError
    return value


def _bool(value: object) -> bool:
    if type(value) is not bool:
        raise ValueError
    return value


def _decimal(value: object) -> Decimal:
    if not isinstance(value, str):
        raise ValueError
    result = Decimal(value)
    if not result.is_finite() or decimal_text(result) != value:
        raise ValueError
    return result


def _optional_decimal(value: object) -> Decimal | None:
    return None if value is None else _decimal(value)


def _datetime(value: object) -> datetime:
    raw = _string(value)
    parsed = datetime.fromisoformat(raw)
    canonical = require_utc(parsed, field_name="datetime")
    if canonical.isoformat() != raw:
        raise ValueError
    return canonical


def _optional_datetime(value: object) -> datetime | None:
    return None if value is None else _datetime(value)
