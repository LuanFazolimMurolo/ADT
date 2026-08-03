"""Deterministic replay service for local paper-trading sessions."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.backtesting.domain import EvaluationBacktestConfig
from app.backtesting.engine import DeterministicBacktestEngine
from app.backtesting.serialization import canonical_json_bytes
from app.backtesting.strategy import BacktestStrategy
from app.market_data.datasets import DatasetSnapshot
from app.market_data.domain import Candle, DataRange, Exchange, MarketType, require_utc
from app.market_data.storage import canonical_candle_bytes, validate_candle_serialization
from app.paper_trading.domain import (
    PaperCandleBatch,
    PaperRunAction,
    PaperRunResult,
    PaperSessionConfig,
    PaperSessionState,
    build_paper_session_state,
    paper_session_id,
    validate_paper_state_against_config,
)
from app.paper_trading.errors import (
    InvalidPaperSessionError,
    PaperSessionVerificationError,
)
from app.paper_trading.repository import PaperTradingRepository
from app.paper_trading.source import (
    InMemoryPaperSnapshotReader,
    LocalRawPaperCandleSource,
    PaperCandleSource,
)
from app.strategies.catalog import builtin_indicator_capabilities
from app.strategies.registry import StrategyPluginRegistry


class PaperTradingService:
    def __init__(
        self,
        data_dir: Path,
        *,
        repository: PaperTradingRepository | None = None,
        source: PaperCandleSource | None = None,
        registry: StrategyPluginRegistry | None = None,
        clock: Callable[[], datetime] | None = None,
        lock_timeout_seconds: float = 10,
        lock_stale_after_seconds: float = 3_600,
    ) -> None:
        self._repository = repository or PaperTradingRepository(
            data_dir,
            lock_timeout_seconds=lock_timeout_seconds,
            lock_stale_after_seconds=lock_stale_after_seconds,
        )
        self._source = source or LocalRawPaperCandleSource(
            data_dir,
            lock_timeout_seconds=lock_timeout_seconds,
            lock_stale_after_seconds=lock_stale_after_seconds,
        )
        self._registry = registry or StrategyPluginRegistry.builtins()
        self._clock = clock or (lambda: datetime.now(UTC))

    def create(self, config: PaperSessionConfig) -> PaperSessionConfig:
        paper_session_id(config)
        self._build_strategy(config)
        return self._repository.create(config)

    def status(self, session_id: str) -> PaperSessionState | None:
        config = self._repository.load_config(session_id)
        if paper_session_id(config) != session_id:
            raise PaperSessionVerificationError()
        state = self._repository.load_state(session_id)
        if state is not None:
            validate_paper_state_against_config(state, config)
        return state

    def run_once(self, session_id: str) -> PaperRunResult:
        with self._repository.lock(session_id) as lease:
            config = self._repository.load_config(session_id)
            batch = self._source.load(config)
            self._validate_batch(config, batch)
            existing = self._repository.load_state(session_id)
            if existing is not None:
                validate_paper_state_against_config(existing, config)
            if (
                existing is not None
                and existing.data_range == batch.data_range
                and existing.source_checksum == batch.source_checksum
            ):
                return PaperRunResult(PaperRunAction.NOOP, existing)
            state = self._execute(config, batch)
            published = self._repository.publish_state(config, state, lease=lease)
            return PaperRunResult(PaperRunAction.UPDATED, published)

    def verify(self, session_id: str) -> PaperSessionState:
        config = self._repository.load_config(session_id)
        persisted = self._repository.load_state(session_id)
        if persisted is None:
            raise PaperSessionVerificationError("A sessão ainda não possui estado executado.")
        validate_paper_state_against_config(persisted, config)
        batch = self._source.load(config, end=persisted.data_range.end)
        self._validate_batch(config, batch)
        if batch.source_checksum != persisted.source_checksum:
            raise PaperSessionVerificationError("Os candles usados pela sessão foram alterados.")
        rebuilt = self._execute(
            config,
            batch,
            replayed_at=persisted.replayed_at,
            dataset_version_override=persisted.dataset_version,
        )
        if rebuilt != persisted:
            raise PaperSessionVerificationError()
        return persisted

    def _execute(
        self,
        config: PaperSessionConfig,
        batch: PaperCandleBatch,
        *,
        replayed_at: datetime | None = None,
        dataset_version_override: str | None = None,
    ) -> PaperSessionState:
        self._validate_batch(config, batch)
        replay_time = require_utc(
            replayed_at or self._clock(),
            field_name="replayed_at",
        )
        snapshot_id = hashlib.sha256(
            b"adt-paper-snapshot-v1\x00"
            + canonical_json_bytes(
                {
                    "session_id": paper_session_id(config),
                    "source_checksum": batch.source_checksum,
                    "start": batch.data_range.start.isoformat(),
                    "end": batch.data_range.end.isoformat(),
                }
            )
        ).hexdigest()
        snapshot = DatasetSnapshot(
            snapshot_id=snapshot_id,
            dataset_key=f"raw:binance:spot:{config.pair.symbol}:{config.timeframe.code}",
            dataset_version=dataset_version_override or batch.dataset_version,
            checksum=batch.source_checksum,
            data_range=batch.data_range,
            partitions=(),
            manifest_path="paper-trading/in-memory",
            created_at=replay_time.isoformat(),
        )
        strategy = self._build_strategy(config)
        backtest_config = EvaluationBacktestConfig(
            snapshot_id=snapshot_id,
            data_range=batch.data_range,
            evaluation_range=DataRange(config.start_at, batch.data_range.end),
            strategy_lifecycle_version=config.strategy_lifecycle_version,
            strategy=config.strategy,
            initial_capital=config.initial_capital,
            execution=config.execution,
            constraints=config.constraints,
            risk_limits=config.risk_limits,
            history_window=config.history_window,
            max_candles=config.max_candles,
            max_orders=config.max_orders,
            max_events=config.max_events,
            engine_version=config.engine_version,
            schema_version=2,
        )
        reader = InMemoryPaperSnapshotReader(snapshot, batch.candles)
        execution = DeterministicBacktestEngine(reader).run(
            backtest_config,
            strategy,
            cancel_open_orders_at_end=False,
        )
        effective_batch = (
            batch
            if dataset_version_override is None
            else PaperCandleBatch(
                data_range=batch.data_range,
                dataset_version=dataset_version_override,
                source_checksum=batch.source_checksum,
                candles=batch.candles,
            )
        )
        state = build_paper_session_state(
            config=config,
            batch=effective_batch,
            candles_processed=execution.candles_processed,
            orders=execution.orders,
            fills=execution.fills,
            portfolio=execution.final_portfolio,
            risk_halt=execution.risk_halt,
            replayed_at=replay_time,
        )
        validate_paper_state_against_config(state, config)
        return state

    @staticmethod
    def _validate_batch(config: PaperSessionConfig, batch: PaperCandleBatch) -> None:
        paper_session_id(config)
        if not isinstance(batch, PaperCandleBatch):
            raise PaperSessionVerificationError("A fonte retornou um lote inválido.")
        try:
            candidate_batch = PaperCandleBatch(
                data_range=batch.data_range,
                dataset_version=batch.dataset_version,
                source_checksum=batch.source_checksum,
                candles=batch.candles,
            )
            if candidate_batch != batch:
                raise ValueError
            if (
                batch.data_range.start != config.context_start
                or batch.data_range.end <= config.start_at
                or not config.timeframe.validate_open_time(batch.data_range.start)
                or not config.timeframe.validate_open_time(batch.data_range.end)
            ):
                raise ValueError
            expected_count = (
                batch.data_range.end - batch.data_range.start
            ) // config.timeframe.duration
            if (
                expected_count < 1
                or expected_count > config.max_candles
                or len(batch.candles) != expected_count
            ):
                raise ValueError
            digest = hashlib.sha256()
            expected_open = batch.data_range.start
            for candle in batch.candles:
                validate_candle_serialization(candle)
                candidate = Candle(
                    exchange=candle.exchange,
                    market_type=candle.market_type,
                    symbol=candle.symbol,
                    timeframe=candle.timeframe,
                    open_time=candle.open_time,
                    close_time=candle.close_time,
                    open=candle.open,
                    high=candle.high,
                    low=candle.low,
                    close=candle.close,
                    volume=candle.volume,
                    quote_volume=candle.quote_volume,
                    trade_count=candle.trade_count,
                    is_closed=candle.is_closed,
                    source=candle.source,
                )
                if (
                    candidate != candle
                    or candle.exchange is not Exchange.BINANCE
                    or candle.market_type is not MarketType.SPOT
                    or candle.symbol != config.pair.symbol
                    or candle.timeframe != config.timeframe
                    or candle.open_time != expected_open
                    or candle.close_time
                    not in {
                        expected_open + config.timeframe.duration,
                        expected_open + config.timeframe.duration - timedelta(milliseconds=1),
                    }
                    or not candle.is_closed
                ):
                    raise ValueError
                digest.update(canonical_candle_bytes(candle))
                expected_open += config.timeframe.duration
            if expected_open != batch.data_range.end or digest.hexdigest() != batch.source_checksum:
                raise ValueError
        except PaperSessionVerificationError:
            raise
        except Exception:
            raise PaperSessionVerificationError(
                "A fonte de candles diverge do contrato da sessão."
            ) from None

    def _build_strategy(self, config: PaperSessionConfig) -> BacktestStrategy:
        try:
            plugin = self._registry.resolve(config.strategy.name, config.strategy.version)
            if plugin.descriptor.lifecycle_version != config.strategy_lifecycle_version:
                raise InvalidPaperSessionError("O lifecycle da estratégia diverge da sessão.")
            strategy = self._registry.build(
                config.strategy.name,
                config.strategy.version,
                dict(config.strategy.parameters),
                available_indicators=builtin_indicator_capabilities(),
            )
            if strategy.descriptor != config.strategy:
                raise InvalidPaperSessionError("A estratégia diverge da configuração da sessão.")
            return strategy
        except InvalidPaperSessionError:
            raise
        except Exception as error:
            raise InvalidPaperSessionError(str(error)) from None
