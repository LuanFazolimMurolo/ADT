from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass, field, fields, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.backtesting.domain import (
    ExecutionAssumptions,
    FeeModel,
    Fill,
    InstrumentConstraints,
    OrderIntent,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSizedExecutionAssumptions,
    PositionSizingKind,
    PositionSizingPolicy,
    RiskLimits,
    SlippageModel,
    StopLossKind,
    StopLossPolicy,
    StopLossRiskLimits,
    StrategyDescriptor,
    StrategyParameterValue,
    position_sizing_policy_for,
    stop_loss_policy_for,
)
from app.backtesting.serialization import canonical_json_bytes
from app.backtesting.strategy import StrategyContext
from app.market_data.domain import Candle, DataRange, Exchange, MarketType, TradingPair
from app.market_data.storage import canonical_candle_bytes
from app.market_data.timeframes import get_timeframe
from app.paper_trading.commands import configure_paper_trading_parser
from app.paper_trading.documents import (
    decode_paper_config,
    decode_paper_state,
    decode_paper_state_summary,
    encode_paper_config,
    encode_paper_state,
    encode_paper_state_summary,
)
from app.paper_trading.domain import (
    MAX_PAPER_DOCUMENT_BYTES,
    PaperCandleBatch,
    PaperRunAction,
    PaperRunResult,
    PaperSessionConfig,
    PaperSessionState,
    paper_session_id,
    paper_state_id_from_payload,
    validate_paper_state_against_config,
)
from app.paper_trading.errors import (
    InvalidPaperSessionError,
    PaperSessionConflictError,
    PaperSessionCorruptError,
    PaperSessionVerificationError,
)
from app.paper_trading.query import PaperTradingReadService
from app.paper_trading.repository import PaperTradingRepository
from app.paper_trading.service import PaperTradingService
from app.strategies.domain import (
    StrategyParameterKind,
    StrategyParameterSpec,
    StrategyPluginDescriptor,
)
from app.strategies.registry import StrategyPluginRegistry


@dataclass(slots=True)
class FixedBuyStrategy:
    quantity: Decimal
    descriptor: StrategyDescriptor = field(init=False)
    submitted: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self.descriptor = StrategyDescriptor("paper-buy-test", "1", (("quantity", self.quantity),))

    def on_start(self, context: StrategyContext) -> tuple[OrderIntent, ...]:
        del context
        self.submitted = False
        return ()

    def on_candle(self, context: StrategyContext, candle: Candle) -> tuple[OrderIntent, ...]:
        del context, candle
        if self.submitted:
            return ()
        self.submitted = True
        return (
            OrderIntent(
                side=OrderSide.BUY,
                order_type=OrderType.MARKET,
                quantity=self.quantity,
            ),
        )

    def on_fill(self, context: StrategyContext, fill: Fill) -> tuple[OrderIntent, ...]:
        del context, fill
        return ()

    def on_end(self, context: StrategyContext) -> None:
        del context


@dataclass(frozen=True, slots=True)
class BuyPlugin:
    descriptor: StrategyPluginDescriptor = StrategyPluginDescriptor(
        name="paper-buy-test",
        version="1",
        description="Test-only fixed-quantity paper strategy.",
        parameters=(StrategyParameterSpec("quantity", StrategyParameterKind.DECIMAL),),
    )

    def build(
        self,
        parameters: tuple[tuple[str, StrategyParameterValue], ...],
    ) -> FixedBuyStrategy:
        values = dict(parameters)
        quantity = values["quantity"]
        if not isinstance(quantity, Decimal):
            raise ValueError
        return FixedBuyStrategy(quantity)


class FakeSource:
    def __init__(self, candles: tuple[Candle, ...]) -> None:
        self.candles = candles
        self.loads = 0

    def load(self, config: PaperSessionConfig, *, end: datetime | None = None) -> PaperCandleBatch:
        self.loads += 1
        selected = self.candles
        if end is not None:
            selected = tuple(item for item in selected if item.open_time < end)
        start = config.context_start
        selected = tuple(item for item in selected if item.open_time >= start)
        final_end = selected[-1].open_time + config.timeframe.duration
        digest = hashlib.sha256()
        for candle in selected:
            digest.update(canonical_candle_bytes(candle))
        return PaperCandleBatch(
            data_range=DataRange(start, final_end),
            dataset_version=hashlib.sha256(b"dataset" + digest.digest()).hexdigest(),
            source_checksum=digest.hexdigest(),
            candles=selected,
        )


def _candle(index: int, close: str = "100") -> Candle:
    timeframe = get_timeframe("1m")
    opened = datetime(2026, 8, 1, tzinfo=UTC) + index * timeframe.duration
    price = Decimal(close)
    return Candle(
        exchange=Exchange.BINANCE,
        market_type=MarketType.SPOT,
        symbol="BTC/USDT",
        timeframe=timeframe,
        open_time=opened,
        close_time=opened + timeframe.duration,
        open=price,
        high=price + Decimal("1"),
        low=price - Decimal("1"),
        close=price,
        volume=Decimal("10"),
        quote_volume=Decimal("1000"),
        trade_count=10,
        is_closed=True,
        source="test",
    )


def _config(*, warmup: int = 0, lifecycle: int = 1) -> PaperSessionConfig:
    descriptor = StrategyDescriptor(
        "paper-buy-test",
        "1",
        (("quantity", Decimal("0.1")),),
    )
    return PaperSessionConfig(
        pair=TradingPair("BTC", "USDT"),
        timeframe=get_timeframe("1m"),
        start_at=datetime(2026, 8, 1, tzinfo=UTC) + warmup * timedelta(minutes=1),
        warmup_candles=warmup,
        strategy=descriptor,
        strategy_lifecycle_version=lifecycle,
        initial_capital=Decimal("1000"),
        execution=ExecutionAssumptions(
            fees=FeeModel(Decimal("0"), Decimal("0")),
            slippage=SlippageModel(fixed_bps=Decimal("0")),
            force_close_at_end=False,
        ),
        constraints=InstrumentConstraints(
            minimum_quantity=Decimal("0.001"),
            quantity_step=Decimal("0.001"),
            price_tick=Decimal("0.01"),
            minimum_notional=Decimal("1"),
        ),
        risk_limits=RiskLimits(max_open_orders=10, max_total_orders=100),
        history_window=20,
        max_candles=100,
        max_orders=100,
        max_events=1000,
        engine_version="paper-test",
    )


def _service(tmp_path: Path, source: FakeSource) -> PaperTradingService:
    registry = StrategyPluginRegistry((BuyPlugin(),))
    return PaperTradingService(
        tmp_path,
        source=source,
        registry=registry,
        clock=lambda: datetime(2026, 8, 2, tzinfo=UTC),
    )


def test_config_rejects_lifecycle_one_warmup() -> None:
    with pytest.raises(InvalidPaperSessionError):
        _config(warmup=1, lifecycle=1)


def test_config_rejects_force_close() -> None:
    config = _config()
    with pytest.raises(InvalidPaperSessionError):
        PaperSessionConfig(
            **{
                **{item.name: getattr(config, item.name) for item in fields(config)},
                "execution": ExecutionAssumptions(
                    fees=config.execution.fees,
                    slippage=config.execution.slippage,
                    force_close_at_end=True,
                ),
            }
        )


def test_config_and_state_strict_round_trip(tmp_path: Path) -> None:
    config = _config()
    assert decode_paper_config(encode_paper_config(config)) == config
    source = FakeSource((_candle(0), _candle(1)))
    service = _service(tmp_path, source)
    service.create(config)
    state = service.run_once(paper_session_id(config)).state
    assert decode_paper_state(encode_paper_state(state)) == state


def test_document_rejects_extra_field() -> None:
    raw = encode_paper_config(_config())
    payload = json.loads(raw)
    payload["extra"] = True
    with pytest.raises(InvalidPaperSessionError):
        decode_paper_config(json.dumps(payload).encode())


def test_create_is_idempotent_and_distinct_config_gets_distinct_identity(tmp_path: Path) -> None:
    repository = PaperTradingRepository(tmp_path)
    config = _config()
    assert repository.create(config) == config
    assert repository.create(config) == config
    changed = PaperSessionConfig(
        **{
            **{item.name: getattr(config, item.name) for item in fields(config)},
            "initial_capital": Decimal("2000"),
        }
    )
    # Different logical config receives a different session id, therefore it is valid.
    assert repository.create(changed) == changed


def test_run_once_preserves_open_order_at_cycle_boundary_and_fills_next_cycle(
    tmp_path: Path,
) -> None:
    source = FakeSource((_candle(0),))
    service = _service(tmp_path, source)
    config = _config()
    session_id = paper_session_id(config)
    service.create(config)

    first = service.run_once(session_id)
    assert first.action is PaperRunAction.UPDATED
    assert len(first.state.orders) == 1
    assert first.state.orders[0].status is OrderStatus.OPEN
    assert not first.state.fills

    source.candles = (_candle(0), _candle(1, "101"))
    second = service.run_once(session_id)
    assert second.action is PaperRunAction.UPDATED
    assert second.state.orders[0].status is OrderStatus.FILLED
    assert len(second.state.fills) == 1
    assert second.state.portfolio.base_quantity == Decimal("0.1")


def test_run_once_accepts_native_binance_close_timestamp(tmp_path: Path) -> None:
    candle = replace(
        _candle(0),
        close_time=_candle(0).close_time - timedelta(milliseconds=1),
    )
    source = FakeSource((candle,))
    service = _service(tmp_path, source)
    config = _config()
    session_id = paper_session_id(config)
    service.create(config)

    result = service.run_once(session_id)

    assert result.action is PaperRunAction.UPDATED
    assert result.state.candles_processed == 1


def test_unchanged_source_returns_noop(tmp_path: Path) -> None:
    source = FakeSource((_candle(0), _candle(1)))
    service = _service(tmp_path, source)
    config = _config()
    session_id = paper_session_id(config)
    service.create(config)
    first = service.run_once(session_id)
    second = service.run_once(session_id)
    assert first.action is PaperRunAction.UPDATED
    assert second.action is PaperRunAction.NOOP
    assert second.state == first.state


def test_verify_replays_exact_persisted_range(tmp_path: Path) -> None:
    source = FakeSource((_candle(0), _candle(1)))
    service = _service(tmp_path, source)
    config = _config()
    session_id = paper_session_id(config)
    service.create(config)
    state = service.run_once(session_id).state
    assert service.verify(session_id) == state


def test_verify_detects_changed_historical_candle(tmp_path: Path) -> None:
    source = FakeSource((_candle(0), _candle(1)))
    service = _service(tmp_path, source)
    config = _config()
    session_id = paper_session_id(config)
    service.create(config)
    service.run_once(session_id)
    source.candles = (_candle(0, "99"), _candle(1))
    with pytest.raises(PaperSessionVerificationError):
        service.verify(session_id)


def test_corrupt_state_is_rejected(tmp_path: Path) -> None:
    source = FakeSource((_candle(0), _candle(1)))
    service = _service(tmp_path, source)
    config = _config()
    session_id = paper_session_id(config)
    service.create(config)
    service.run_once(session_id)
    state_path = tmp_path / "market" / "paper-trading" / session_id / "state.json"
    state_path.write_text("{}")
    with pytest.raises(PaperSessionCorruptError):
        service.status(session_id)


def test_repository_rejects_state_regression(tmp_path: Path) -> None:
    source = FakeSource((_candle(0), _candle(1)))
    service = _service(tmp_path, source)
    config = _config()
    session_id = paper_session_id(config)
    service.create(config)
    later = service.run_once(session_id).state
    source.candles = (_candle(0),)
    earlier = service._execute(config, source.load(config))  # noqa: SLF001
    repository = service._repository  # noqa: SLF001
    with repository.lock(session_id) as lease:
        with pytest.raises(PaperSessionConflictError):
            repository.publish_state(config, earlier, lease=lease)
    assert repository.load_state(session_id) == later


def test_hostile_state_checksum_is_rejected(tmp_path: Path) -> None:
    source = FakeSource((_candle(0), _candle(1)))
    service = _service(tmp_path, source)
    config = _config()
    session_id = paper_session_id(config)
    service.create(config)
    state = service.run_once(session_id).state
    object.__setattr__(state, "candles_processed", state.candles_processed + 1)
    with pytest.raises(PaperSessionCorruptError):
        decode_paper_state(encode_paper_state(state))


def _state_document(state: PaperSessionState) -> dict[str, object]:
    decoded = json.loads(encode_paper_state(state))
    assert isinstance(decoded, dict)
    return decoded


def _resign_state_payload(payload: dict[str, object]) -> bytes:
    semantic = {
        key: value
        for key, value in payload.items()
        if key not in {"replayed_at", "state_id", "checksum"}
    }
    state_id = paper_state_id_from_payload(semantic)
    payload["state_id"] = state_id
    payload["checksum"] = hashlib.sha256(
        canonical_json_bytes(
            {
                **semantic,
                "replayed_at": payload["replayed_at"],
                "state_id": state_id,
            }
        )
    ).hexdigest()
    return canonical_json_bytes({"state": payload, "checksum": payload["checksum"]})


def test_config_rejects_float_lifecycle_and_bool_schema() -> None:
    config = _config()
    values = {item.name: getattr(config, item.name) for item in fields(config)}
    with pytest.raises(InvalidPaperSessionError):
        PaperSessionConfig(**{**values, "strategy_lifecycle_version": 1.0})
    with pytest.raises(InvalidPaperSessionError):
        PaperSessionConfig(**{**values, "schema_version": True})


def test_config_decoder_rejects_resigned_nested_extra_and_noncanonical_decimal() -> None:
    document = json.loads(encode_paper_config(_config()))
    payload = document["config"]
    payload["execution"]["extra"] = True
    document["checksum"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    document["session_id"] = hashlib.sha256(
        b"adt-paper-session-v1\x00" + canonical_json_bytes(payload)
    ).hexdigest()
    with pytest.raises(InvalidPaperSessionError):
        decode_paper_config(canonical_json_bytes(document))

    document = json.loads(encode_paper_config(_config()))
    payload = document["config"]
    payload["initial_capital"] = "1000.0"
    document["checksum"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    document["session_id"] = hashlib.sha256(
        b"adt-paper-session-v1\x00" + canonical_json_bytes(payload)
    ).hexdigest()
    with pytest.raises(InvalidPaperSessionError):
        decode_paper_config(canonical_json_bytes(document))


def test_state_decoder_rejects_resigned_nested_extra(tmp_path: Path) -> None:
    source = FakeSource((_candle(0),))
    service = _service(tmp_path, source)
    config = _config()
    service.create(config)
    state = service.run_once(paper_session_id(config)).state
    document = _state_document(state)
    payload = document["state"]
    payload["orders"][0]["extra"] = True
    with pytest.raises(PaperSessionCorruptError):
        decode_paper_state(_resign_state_payload(payload))


def test_encode_state_fails_fast_after_hostile_mutation(tmp_path: Path) -> None:
    source = FakeSource((_candle(0), _candle(1)))
    service = _service(tmp_path, source)
    config = _config()
    service.create(config)
    state = service.run_once(paper_session_id(config)).state
    object.__setattr__(state, "risk_halt", 1)
    with pytest.raises(PaperSessionCorruptError):
        encode_paper_state(state)


def test_state_against_config_rejects_fully_resigned_wrong_context(tmp_path: Path) -> None:
    source = FakeSource((_candle(0), _candle(1)))
    service = _service(tmp_path, source)
    config = _config()
    service.create(config)
    state = service.run_once(paper_session_id(config)).state
    document = _state_document(state)
    payload = document["state"]
    payload["data_range"]["start"] = (
        state.data_range.start - config.timeframe.duration
    ).isoformat()
    resigned = decode_paper_state(_resign_state_payload(payload))
    with pytest.raises(PaperSessionVerificationError):
        validate_paper_state_against_config(resigned, config)


def test_service_rejects_hostile_batch_before_strategy_execution(tmp_path: Path) -> None:
    source = FakeSource((_candle(0), _candle(1)))
    service = _service(tmp_path, source)
    config = _config()
    session_id = paper_session_id(config)
    service.create(config)
    original_load = source.load

    def hostile_load(
        selected_config: PaperSessionConfig,
        *,
        end: datetime | None = None,
    ) -> PaperCandleBatch:
        batch = original_load(selected_config, end=end)
        return replace(batch, source_checksum="0" * 64)

    source.load = hostile_load  # type: ignore[method-assign]
    with pytest.raises(PaperSessionVerificationError):
        service.run_once(session_id)
    assert service.status(session_id) is None


def test_service_rejects_gapped_or_wrong_identity_batch(tmp_path: Path) -> None:
    config = _config()
    service = _service(tmp_path, FakeSource((_candle(0), _candle(2))))
    session_id = paper_session_id(config)
    service.create(config)
    with pytest.raises(PaperSessionVerificationError):
        service.run_once(session_id)

    wrong = replace(_candle(0), symbol="ETH/USDT")
    service = _service(tmp_path / "wrong", FakeSource((wrong,)))
    service.create(config)
    with pytest.raises(PaperSessionVerificationError):
        service.run_once(session_id)


def test_repository_rejects_state_from_another_config(tmp_path: Path) -> None:
    source = FakeSource((_candle(0), _candle(1)))
    service = _service(tmp_path, source)
    config = _config()
    session_id = paper_session_id(config)
    service.create(config)
    state = service.run_once(session_id).state
    changed = PaperSessionConfig(
        **{
            **{item.name: getattr(config, item.name) for item in fields(config)},
            "initial_capital": Decimal("2000"),
        }
    )
    repository = PaperTradingRepository(tmp_path)
    repository.create(changed)
    with repository.lock(paper_session_id(changed)) as lease:
        with pytest.raises(PaperSessionConflictError):
            repository.publish_state(changed, state, lease=lease)


def test_paper_run_result_rejects_raw_enum_or_state() -> None:
    with pytest.raises(InvalidPaperSessionError):
        PaperRunResult("UPDATED", object())  # type: ignore[arg-type]


def test_cli_parser_exposes_paper_trading_commands() -> None:
    parser = argparse.ArgumentParser()
    configure_paper_trading_parser(parser)
    created = parser.parse_args(
        [
            "create",
            "--symbol",
            "BTC/USDT",
            "--timeframe",
            "1m",
            "--start",
            "2026-08-01T00:00:00Z",
            "--strategy",
            "no-op",
            "--strategy-version",
            "1",
            "--initial-capital",
            "1000",
            "--stop-loss",
            "fixed_percent",
            "--stop-loss-value",
            "5",
            "--yes",
        ]
    )
    assert created.paper_command == "create"
    assert created.initial_capital == Decimal("1000")
    assert created.stop_loss == "fixed_percent"
    assert created.stop_loss_value == Decimal("5")
    status = parser.parse_args(["status", "--session-id", "a" * 64])
    assert status.paper_command == "status"


def test_documents_reject_duplicate_keys_and_oversized_input() -> None:
    duplicate = b'{"config":{},"config":{},"checksum":"x","session_id":"y"}'
    with pytest.raises(InvalidPaperSessionError):
        decode_paper_config(duplicate)
    with pytest.raises(PaperSessionCorruptError):
        decode_paper_state(b" " * (MAX_PAPER_DOCUMENT_BYTES + 1))


def _config_with_capital(value: str) -> PaperSessionConfig:
    return replace(_config(), initial_capital=Decimal(value))


def test_read_service_decodes_only_configs_inside_requested_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = PaperTradingRepository(tmp_path)
    configs = tuple(_config_with_capital(value) for value in ("1000", "2000", "3000"))
    for config in configs:
        repository.create(config)
    expected_ids = tuple(sorted(paper_session_id(config) for config in configs))
    opened: list[str] = []
    original = PaperTradingRepository._read_config_path

    def tracked(path: Path) -> PaperSessionConfig:
        opened.append(path.parent.name)
        return original(path)

    monkeypatch.setattr(
        PaperTradingRepository,
        "_read_config_path",
        staticmethod(tracked),
    )
    page = PaperTradingReadService(repository).list_sessions(page=2, page_size=1)

    assert page.total == 3
    assert page.total_pages == 3
    assert tuple(item.session_id for item in page.items) == (expected_ids[1],)
    assert opened == [expected_ids[1]]


def test_read_service_uses_lightweight_summary_without_decoding_full_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = FakeSource((_candle(0), _candle(1)))
    service = _service(tmp_path, source)
    configs = tuple(_config_with_capital(value) for value in ("1000", "2000"))
    states: dict[str, PaperSessionState] = {}
    for config in configs:
        session_id = paper_session_id(config)
        service.create(config)
        states[session_id] = service.run_once(session_id).state
    selected_id = sorted(states)[0]

    def forbidden(_path: Path) -> PaperSessionState:
        raise AssertionError("a listagem não deve decodificar o estado completo")

    monkeypatch.setattr(
        PaperTradingRepository,
        "_read_state_path",
        staticmethod(forbidden),
    )
    page = PaperTradingReadService(service._repository).list_sessions(  # noqa: SLF001
        page=1,
        page_size=1,
    )

    assert tuple(item.session_id for item in page.items) == (selected_id,)
    summary = page.items[0].summary
    assert summary is not None
    assert summary.orders_count == len(states[selected_id].orders)
    assert summary.fills_count == len(states[selected_id].fills)


def test_legacy_state_summary_is_migrated_once_for_selected_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = FakeSource((_candle(0), _candle(1)))
    service = _service(tmp_path, source)
    config = _config()
    session_id = paper_session_id(config)
    service.create(config)
    service.run_once(session_id)
    summary_path = tmp_path / "market" / "paper-trading" / session_id / "summary.json"
    summary_path.unlink()
    calls = 0
    original = PaperTradingRepository._read_state_path

    def tracked(path: Path) -> PaperSessionState:
        nonlocal calls
        calls += 1
        return original(path)

    monkeypatch.setattr(
        PaperTradingRepository,
        "_read_state_path",
        staticmethod(tracked),
    )
    read = PaperTradingReadService(service._repository)  # noqa: SLF001
    assert read.list_sessions(page=1, page_size=1).total == 1
    assert summary_path.is_file()
    assert calls == 1
    assert read.list_sessions(page=1, page_size=1).total == 1
    assert calls == 1


def test_corrupt_or_divergent_state_summary_is_rejected(tmp_path: Path) -> None:
    source = FakeSource((_candle(0), _candle(1)))
    service = _service(tmp_path, source)
    config = _config()
    session_id = paper_session_id(config)
    service.create(config)
    service.run_once(session_id)
    summary_path = tmp_path / "market" / "paper-trading" / session_id / "summary.json"
    read = PaperTradingReadService(service._repository)  # noqa: SLF001

    original = summary_path.read_bytes()
    summary_path.write_text("{}")
    with pytest.raises(PaperSessionCorruptError):
        read.list_sessions(page=1, page_size=1)

    summary_path.write_bytes(original)
    summary = decode_paper_state_summary(original)
    divergent = replace(summary, state_checksum="0" * 64)
    summary_path.write_bytes(encode_paper_state_summary(divergent))
    with pytest.raises(PaperSessionCorruptError):
        read.list_sessions(page=1, page_size=1)


def test_paper_config_round_trip_preserves_position_sizing() -> None:
    config = _config()
    updated = PaperSessionConfig(
        **{
            **{item.name: getattr(config, item.name) for item in fields(config)},
            "execution": PositionSizedExecutionAssumptions(
                fees=config.execution.fees,
                slippage=config.execution.slippage,
                position_sizing=PositionSizingPolicy(
                    PositionSizingKind.EQUITY_PERCENT,
                    Decimal("20"),
                ),
            ),
        }
    )

    assert decode_paper_config(encode_paper_config(updated)) == updated
    assert paper_session_id(updated) != paper_session_id(config)


def test_paper_config_decoder_accepts_legacy_execution_without_sizing() -> None:
    raw = json.loads(encode_paper_config(_config()))

    assert "position_sizing" not in raw["config"]["execution"]
    decoded = decode_paper_config(json.dumps(raw).encode())
    assert position_sizing_policy_for(decoded.execution) == PositionSizingPolicy()


def test_paper_config_decoder_rejects_redundant_explicit_sizing() -> None:
    raw = json.loads(encode_paper_config(_config()))
    raw["config"]["execution"]["position_sizing"] = {
        "kind": "explicit_quantity",
        "value": None,
        "minimum_quote_reserve": "0",
    }

    with pytest.raises(InvalidPaperSessionError):
        decode_paper_config(json.dumps(raw).encode())


def test_paper_config_round_trip_preserves_stop_loss() -> None:
    config = _config()
    updated = PaperSessionConfig(
        **{
            **{item.name: getattr(config, item.name) for item in fields(config)},
            "risk_limits": StopLossRiskLimits(
                max_open_orders=config.risk_limits.max_open_orders,
                max_total_orders=config.risk_limits.max_total_orders,
                stop_loss=StopLossPolicy(
                    StopLossKind.FIXED_PERCENT,
                    Decimal("5"),
                ),
            ),
        }
    )

    assert decode_paper_config(encode_paper_config(updated)) == updated
    assert paper_session_id(updated) != paper_session_id(config)


def test_paper_config_decoder_accepts_legacy_risk_without_stop_loss() -> None:
    raw = json.loads(encode_paper_config(_config()))

    assert "stop_loss" not in raw["config"]["risk_limits"]
    decoded = decode_paper_config(json.dumps(raw).encode())
    assert stop_loss_policy_for(decoded.risk_limits) == StopLossPolicy()


def test_paper_config_decoder_rejects_redundant_disabled_stop_loss() -> None:
    raw = json.loads(encode_paper_config(_config()))
    raw["config"]["risk_limits"]["stop_loss"] = {
        "kind": "disabled",
        "value": None,
    }

    with pytest.raises(InvalidPaperSessionError):
        decode_paper_config(json.dumps(raw).encode())
