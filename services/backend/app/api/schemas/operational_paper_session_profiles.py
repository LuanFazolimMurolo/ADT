"""Administrator HTTP contracts for operational paper-session profiles."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal, Self, TypeAlias
from uuid import UUID

from pydantic import (
    AfterValidator,
    BeforeValidator,
    Field,
    PlainSerializer,
    field_validator,
)

from app.api.schemas.common import ApiSchema
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
)
from app.backtesting.serialization import decimal_text
from app.domain.errors import DomainError
from app.indicators.regime import MarketRegimePolicy
from app.market_data.domain import Exchange, MarketType, TradingPair
from app.market_data.timeframes import TIMEFRAMES
from app.operational_mandates import OperationalMandateInstrument
from app.operational_paper_session_profiles import (
    MAX_OPERATIONAL_PAPER_SESSION_PROFILE_CANDLES,
    MAX_OPERATIONAL_PAPER_SESSION_PROFILE_EVENTS,
    MAX_OPERATIONAL_PAPER_SESSION_PROFILE_HISTORY_WINDOW,
    MAX_OPERATIONAL_PAPER_SESSION_PROFILE_IDEMPOTENCY_KEY_LENGTH,
    MAX_OPERATIONAL_PAPER_SESSION_PROFILE_ORDERS,
    MAX_OPERATIONAL_PAPER_SESSION_PROFILE_WARMUP_CANDLES,
    OperationalPaperSessionProfile,
    OperationalPaperSessionProfileCreateIntent,
    OperationalPaperSessionProfileMandateBinding,
    OperationalPaperSessionProfileRevision,
    OperationalPaperSessionProfileSpecification,
    OperationalPaperSessionProfileState,
    OperationalPaperSessionProfileStrategySnapshot,
)
from app.operational_paper_session_profiles.errors import (
    InvalidOperationalPaperSessionProfileSpecificationError,
    OperationalPaperSessionProfileBoundsExceededError,
)

_IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_DECIMAL_INPUT_PATTERN = re.compile(r"-?\d+(?:\.\d+)?\Z")


def _validate_decimal_string_input(value: object) -> object:
    if not isinstance(value, str) or _DECIMAL_INPUT_PATTERN.fullmatch(value) is None:
        raise ValueError("Deterministic decimals must be ordinary base-10 strings.")
    return value


def _validate_finite_decimal(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError("Deterministic decimals must be finite.")
    return value


ProfileDecimal = Annotated[
    Decimal,
    AfterValidator(_validate_finite_decimal),
    PlainSerializer(decimal_text, return_type=str, when_used="json"),
]
ProfileDecimalStringInput = Annotated[
    ProfileDecimal,
    BeforeValidator(_validate_decimal_string_input, json_schema_input_type=str),
]
StrategyParameterScalar: TypeAlias = str | int | bool | None
StrategyParameterType: TypeAlias = Literal["null", "boolean", "integer", "decimal", "string"]


class OperationalPaperSessionProfileMandateBindingRequest(ApiSchema):
    mandate_id: UUID
    approved_revision: int = Field(strict=True, ge=1)
    specification_checksum: str = Field(
        strict=True,
        min_length=64,
        max_length=64,
        pattern=_SHA256_PATTERN,
    )

    def to_domain(self) -> OperationalPaperSessionProfileMandateBinding:
        return OperationalPaperSessionProfileMandateBinding(
            mandate_id=self.mandate_id,
            approved_revision=self.approved_revision,
            specification_checksum=self.specification_checksum,
        )


class OperationalPaperSessionProfileInstrumentRequest(ApiSchema):
    exchange: Exchange
    market_type: MarketType
    base_asset: str = Field(strict=True)
    quote_asset: str = Field(strict=True)

    def to_domain(self) -> OperationalMandateInstrument:
        try:
            pair = TradingPair(self.base_asset, self.quote_asset)
            return OperationalMandateInstrument(
                exchange=self.exchange,
                market_type=self.market_type,
                pair=pair,
            )
        except DomainError:
            raise InvalidOperationalPaperSessionProfileSpecificationError() from None


class OperationalPaperSessionProfileFeeRequest(ApiSchema):
    maker_fee_bps: ProfileDecimalStringInput
    taker_fee_bps: ProfileDecimalStringInput

    def to_domain(self) -> FeeModel:
        return FeeModel(self.maker_fee_bps, self.taker_fee_bps)


class OperationalPaperSessionProfileSlippageRequest(ApiSchema):
    kind: SlippageKind
    fixed_bps: ProfileDecimalStringInput

    def to_domain(self) -> SlippageModel:
        return SlippageModel(kind=self.kind, fixed_bps=self.fixed_bps)


class OperationalPaperSessionProfilePositionSizingRequest(ApiSchema):
    kind: PositionSizingKind
    value: ProfileDecimalStringInput | None = None
    minimum_quote_reserve: ProfileDecimalStringInput

    def to_domain(self) -> PositionSizingPolicy:
        return PositionSizingPolicy(
            kind=self.kind,
            value=self.value,
            minimum_quote_reserve=self.minimum_quote_reserve,
        )


class OperationalPaperSessionProfileExecutionRequest(ApiSchema):
    fees: OperationalPaperSessionProfileFeeRequest
    slippage: OperationalPaperSessionProfileSlippageRequest
    intrabar_policy: IntrabarPolicy
    force_close_at_end: bool = Field(strict=True)
    position_sizing: OperationalPaperSessionProfilePositionSizingRequest | None = None

    def to_domain(self) -> ExecutionAssumptions:
        fees = self.fees.to_domain()
        slippage = self.slippage.to_domain()
        if self.position_sizing is None:
            return ExecutionAssumptions(
                fees=fees,
                slippage=slippage,
                intrabar_policy=self.intrabar_policy,
                force_close_at_end=self.force_close_at_end,
            )
        return PositionSizedExecutionAssumptions(
            fees=fees,
            slippage=slippage,
            intrabar_policy=self.intrabar_policy,
            force_close_at_end=self.force_close_at_end,
            position_sizing=self.position_sizing.to_domain(),
        )


class OperationalPaperSessionProfileInstrumentConstraintsRequest(ApiSchema):
    minimum_quantity: ProfileDecimalStringInput
    quantity_step: ProfileDecimalStringInput
    price_tick: ProfileDecimalStringInput
    minimum_notional: ProfileDecimalStringInput
    maximum_notional: ProfileDecimalStringInput | None = None

    def to_domain(self) -> InstrumentConstraints:
        return InstrumentConstraints(
            minimum_quantity=self.minimum_quantity,
            quantity_step=self.quantity_step,
            price_tick=self.price_tick,
            minimum_notional=self.minimum_notional,
            maximum_notional=self.maximum_notional,
        )


class OperationalPaperSessionProfileStopLossRequest(ApiSchema):
    kind: StopLossKind
    value: ProfileDecimalStringInput | None = None

    def to_domain(self) -> StopLossPolicy:
        return StopLossPolicy(kind=self.kind, value=self.value)


class OperationalPaperSessionProfileRiskLimitsRequest(ApiSchema):
    max_order_notional: ProfileDecimalStringInput | None = None
    max_position_notional: ProfileDecimalStringInput | None = None
    max_open_orders: int = Field(strict=True, ge=1)
    max_total_orders: int = Field(strict=True, ge=1)
    max_drawdown_pct: ProfileDecimalStringInput | None = None
    stop_on_max_drawdown: bool = Field(strict=True)
    allow_all_in: bool = Field(strict=True)
    minimum_quote_reserve: ProfileDecimalStringInput
    stop_loss: OperationalPaperSessionProfileStopLossRequest | None = None

    def to_domain(self) -> RiskLimits:
        if self.stop_loss is None:
            return RiskLimits(
                max_order_notional=self.max_order_notional,
                max_position_notional=self.max_position_notional,
                max_open_orders=self.max_open_orders,
                max_total_orders=self.max_total_orders,
                max_drawdown_pct=self.max_drawdown_pct,
                stop_on_max_drawdown=self.stop_on_max_drawdown,
                allow_all_in=self.allow_all_in,
                minimum_quote_reserve=self.minimum_quote_reserve,
            )
        return StopLossRiskLimits(
            max_order_notional=self.max_order_notional,
            max_position_notional=self.max_position_notional,
            max_open_orders=self.max_open_orders,
            max_total_orders=self.max_total_orders,
            max_drawdown_pct=self.max_drawdown_pct,
            stop_on_max_drawdown=self.stop_on_max_drawdown,
            allow_all_in=self.allow_all_in,
            minimum_quote_reserve=self.minimum_quote_reserve,
            stop_loss=self.stop_loss.to_domain(),
        )


class OperationalPaperSessionProfileMarketRegimeRequest(ApiSchema):
    fast_ema_period: int = Field(strict=True, ge=1)
    slow_ema_period: int = Field(strict=True, ge=1)
    atr_period: int = Field(strict=True, ge=1)
    volatile_atr_ratio: ProfileDecimalStringInput
    trend_strength_threshold: ProfileDecimalStringInput
    schema_version: int = Field(strict=True, ge=1, le=1)

    def to_domain(self) -> MarketRegimePolicy:
        return MarketRegimePolicy(
            fast_ema_period=self.fast_ema_period,
            slow_ema_period=self.slow_ema_period,
            atr_period=self.atr_period,
            volatile_atr_ratio=self.volatile_atr_ratio,
            trend_strength_threshold=self.trend_strength_threshold,
            schema_version=self.schema_version,
        )


class OperationalPaperSessionProfileIntentRequest(ApiSchema):
    name: str = Field(
        strict=True,
        min_length=1,
    )
    description: str = Field(strict=True)
    mandate_binding: OperationalPaperSessionProfileMandateBindingRequest
    selected_instrument: OperationalPaperSessionProfileInstrumentRequest
    timeframe: str = Field(strict=True)
    start_at: datetime
    warmup_candles: int = Field(
        strict=True,
        ge=0,
        le=MAX_OPERATIONAL_PAPER_SESSION_PROFILE_WARMUP_CANDLES,
    )
    strategy_definition_id: UUID
    expected_strategy_definition_revision: int = Field(strict=True, ge=1)
    expected_strategy_parameters_checksum: str = Field(
        strict=True,
        min_length=64,
        max_length=64,
        pattern=_SHA256_PATTERN,
    )
    execution: OperationalPaperSessionProfileExecutionRequest
    instrument_constraints: OperationalPaperSessionProfileInstrumentConstraintsRequest
    risk_limits: OperationalPaperSessionProfileRiskLimitsRequest
    history_window: int = Field(
        strict=True,
        ge=1,
        le=MAX_OPERATIONAL_PAPER_SESSION_PROFILE_HISTORY_WINDOW,
    )
    max_candles: int = Field(
        strict=True,
        ge=1,
        le=MAX_OPERATIONAL_PAPER_SESSION_PROFILE_CANDLES,
    )
    max_orders: int = Field(
        strict=True,
        ge=1,
        le=MAX_OPERATIONAL_PAPER_SESSION_PROFILE_ORDERS,
    )
    max_events: int = Field(
        strict=True,
        ge=1,
        le=MAX_OPERATIONAL_PAPER_SESSION_PROFILE_EVENTS,
    )
    engine_version: str = Field(strict=True)
    market_regime_policy: OperationalPaperSessionProfileMarketRegimeRequest | None = None

    @field_validator("timeframe")
    @classmethod
    def _require_configured_timeframe(cls, value: str) -> str:
        if value not in TIMEFRAMES:
            raise ValueError("Timeframe must be a configured canonical code.")
        return value

    @field_validator("start_at")
    @classmethod
    def _require_utc_start(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None or value.utcoffset() != timedelta(0):
            raise ValueError("start_at must use UTC.")
        return value.astimezone(UTC)

    def to_domain(self) -> OperationalPaperSessionProfileCreateIntent:
        try:
            return OperationalPaperSessionProfileCreateIntent(
                name=self.name,
                description=self.description,
                mandate_binding=self.mandate_binding.to_domain(),
                selected_instrument=self.selected_instrument.to_domain(),
                timeframe=TIMEFRAMES[self.timeframe],
                start_at=self.start_at,
                warmup_candles=self.warmup_candles,
                strategy_definition_id=self.strategy_definition_id,
                expected_strategy_definition_revision=(self.expected_strategy_definition_revision),
                expected_strategy_parameters_checksum=(self.expected_strategy_parameters_checksum),
                execution=self.execution.to_domain(),
                instrument_constraints=self.instrument_constraints.to_domain(),
                risk_limits=self.risk_limits.to_domain(),
                history_window=self.history_window,
                max_candles=self.max_candles,
                max_orders=self.max_orders,
                max_events=self.max_events,
                engine_version=self.engine_version,
                market_regime_policy=(
                    None
                    if self.market_regime_policy is None
                    else self.market_regime_policy.to_domain()
                ),
            )
        except OperationalPaperSessionProfileBoundsExceededError:
            raise
        except InvalidOperationalPaperSessionProfileSpecificationError:
            raise
        except (DomainError, TypeError, ValueError):
            raise InvalidOperationalPaperSessionProfileSpecificationError() from None


class OperationalPaperSessionProfileCreateRequest(ApiSchema):
    intent: OperationalPaperSessionProfileIntentRequest
    idempotency_key: str = Field(
        strict=True,
        min_length=1,
        max_length=MAX_OPERATIONAL_PAPER_SESSION_PROFILE_IDEMPOTENCY_KEY_LENGTH,
        pattern=_IDEMPOTENCY_KEY_PATTERN,
    )


class OperationalPaperSessionProfileReplaceRequest(ApiSchema):
    intent: OperationalPaperSessionProfileIntentRequest
    expected_revision: int = Field(strict=True, ge=1)
    expected_record_version: int = Field(strict=True, ge=1)


class OperationalPaperSessionProfileApproveRequest(ApiSchema):
    expected_revision: int = Field(strict=True, ge=1)
    expected_checksum: str = Field(
        strict=True,
        min_length=64,
        max_length=64,
        pattern=_SHA256_PATTERN,
    )
    expected_record_version: int = Field(strict=True, ge=1)


class OperationalPaperSessionProfileArchiveRequest(ApiSchema):
    expected_record_version: int = Field(strict=True, ge=1)


class OperationalPaperSessionProfileMandateBindingResponse(ApiSchema):
    mandate_id: UUID
    approved_revision: int
    specification_checksum: str

    @classmethod
    def from_domain(
        cls,
        binding: OperationalPaperSessionProfileMandateBinding,
    ) -> Self:
        return cls(
            mandate_id=binding.mandate_id,
            approved_revision=binding.approved_revision,
            specification_checksum=binding.specification_checksum,
        )


class OperationalPaperSessionProfileInstrumentResponse(ApiSchema):
    exchange: Exchange
    market_type: MarketType
    base_asset: str
    quote_asset: str

    @classmethod
    def from_domain(cls, instrument: OperationalMandateInstrument) -> Self:
        return cls(
            exchange=instrument.exchange,
            market_type=instrument.market_type,
            base_asset=instrument.pair.base,
            quote_asset=instrument.pair.quote,
        )


class OperationalPaperSessionProfileFeeResponse(ApiSchema):
    maker_fee_bps: ProfileDecimal
    taker_fee_bps: ProfileDecimal

    @classmethod
    def from_domain(cls, fees: FeeModel) -> Self:
        return cls(maker_fee_bps=fees.maker_fee_bps, taker_fee_bps=fees.taker_fee_bps)


class OperationalPaperSessionProfileSlippageResponse(ApiSchema):
    kind: SlippageKind
    fixed_bps: ProfileDecimal

    @classmethod
    def from_domain(cls, slippage: SlippageModel) -> Self:
        return cls(kind=slippage.kind, fixed_bps=slippage.fixed_bps)


class OperationalPaperSessionProfilePositionSizingResponse(ApiSchema):
    kind: PositionSizingKind
    value: ProfileDecimal | None
    minimum_quote_reserve: ProfileDecimal

    @classmethod
    def from_domain(cls, policy: PositionSizingPolicy) -> Self:
        return cls(
            kind=policy.kind,
            value=policy.value,
            minimum_quote_reserve=policy.minimum_quote_reserve,
        )


class OperationalPaperSessionProfileExecutionResponse(ApiSchema):
    fees: OperationalPaperSessionProfileFeeResponse
    slippage: OperationalPaperSessionProfileSlippageResponse
    intrabar_policy: IntrabarPolicy
    force_close_at_end: bool
    position_sizing: OperationalPaperSessionProfilePositionSizingResponse | None

    @classmethod
    def from_domain(cls, execution: ExecutionAssumptions) -> Self:
        sizing = (
            execution.position_sizing
            if isinstance(execution, PositionSizedExecutionAssumptions)
            else None
        )
        return cls(
            fees=OperationalPaperSessionProfileFeeResponse.from_domain(execution.fees),
            slippage=OperationalPaperSessionProfileSlippageResponse.from_domain(execution.slippage),
            intrabar_policy=execution.intrabar_policy,
            force_close_at_end=execution.force_close_at_end,
            position_sizing=(
                None
                if sizing is None
                else OperationalPaperSessionProfilePositionSizingResponse.from_domain(sizing)
            ),
        )


class OperationalPaperSessionProfileInstrumentConstraintsResponse(ApiSchema):
    minimum_quantity: ProfileDecimal
    quantity_step: ProfileDecimal
    price_tick: ProfileDecimal
    minimum_notional: ProfileDecimal
    maximum_notional: ProfileDecimal | None

    @classmethod
    def from_domain(cls, constraints: InstrumentConstraints) -> Self:
        return cls(
            minimum_quantity=constraints.minimum_quantity,
            quantity_step=constraints.quantity_step,
            price_tick=constraints.price_tick,
            minimum_notional=constraints.minimum_notional,
            maximum_notional=constraints.maximum_notional,
        )


class OperationalPaperSessionProfileStopLossResponse(ApiSchema):
    kind: StopLossKind
    value: ProfileDecimal | None

    @classmethod
    def from_domain(cls, policy: StopLossPolicy) -> Self:
        return cls(kind=policy.kind, value=policy.value)


class OperationalPaperSessionProfileRiskLimitsResponse(ApiSchema):
    max_order_notional: ProfileDecimal | None
    max_position_notional: ProfileDecimal | None
    max_open_orders: int
    max_total_orders: int
    max_drawdown_pct: ProfileDecimal | None
    stop_on_max_drawdown: bool
    allow_all_in: bool
    minimum_quote_reserve: ProfileDecimal
    stop_loss: OperationalPaperSessionProfileStopLossResponse | None

    @classmethod
    def from_domain(cls, risk: RiskLimits) -> Self:
        stop_loss = risk.stop_loss if isinstance(risk, StopLossRiskLimits) else None
        return cls(
            max_order_notional=risk.max_order_notional,
            max_position_notional=risk.max_position_notional,
            max_open_orders=risk.max_open_orders,
            max_total_orders=risk.max_total_orders,
            max_drawdown_pct=risk.max_drawdown_pct,
            stop_on_max_drawdown=risk.stop_on_max_drawdown,
            allow_all_in=risk.allow_all_in,
            minimum_quote_reserve=risk.minimum_quote_reserve,
            stop_loss=(
                None
                if stop_loss is None
                else OperationalPaperSessionProfileStopLossResponse.from_domain(stop_loss)
            ),
        )


class OperationalPaperSessionProfileMarketRegimeResponse(ApiSchema):
    fast_ema_period: int
    slow_ema_period: int
    atr_period: int
    volatile_atr_ratio: ProfileDecimal
    trend_strength_threshold: ProfileDecimal
    schema_version: int

    @classmethod
    def from_domain(cls, policy: MarketRegimePolicy) -> Self:
        return cls(
            fast_ema_period=policy.fast_ema_period,
            slow_ema_period=policy.slow_ema_period,
            atr_period=policy.atr_period,
            volatile_atr_ratio=policy.volatile_atr_ratio,
            trend_strength_threshold=policy.trend_strength_threshold,
            schema_version=policy.schema_version,
        )


class OperationalPaperSessionProfileStrategyParameterResponse(ApiSchema):
    name: str
    type: StrategyParameterType
    value: StrategyParameterScalar

    @classmethod
    def from_domain(cls, name: str, value: object) -> Self:
        if value is None:
            return cls(name=name, type="null", value=None)
        if isinstance(value, bool):
            return cls(name=name, type="boolean", value=value)
        if isinstance(value, int):
            return cls(name=name, type="integer", value=value)
        if isinstance(value, Decimal):
            return cls(name=name, type="decimal", value=decimal_text(value))
        if isinstance(value, str):
            return cls(name=name, type="string", value=value)
        raise TypeError("Unsupported strategy parameter value.")


class OperationalPaperSessionProfileStrategySnapshotResponse(ApiSchema):
    snapshot_schema_version: int
    strategy_definition_id: UUID
    source_revision: int
    plugin_name: str
    plugin_version: str
    plugin_schema_version: int
    strategy_lifecycle_version: int
    parameters: list[OperationalPaperSessionProfileStrategyParameterResponse]
    parameters_checksum: str
    snapshot_checksum: str

    @classmethod
    def from_domain(
        cls,
        snapshot: OperationalPaperSessionProfileStrategySnapshot,
    ) -> Self:
        return cls(
            snapshot_schema_version=snapshot.snapshot_schema_version,
            strategy_definition_id=snapshot.strategy_definition_id,
            source_revision=snapshot.source_revision,
            plugin_name=snapshot.plugin_name,
            plugin_version=snapshot.plugin_version,
            plugin_schema_version=snapshot.plugin_schema_version,
            strategy_lifecycle_version=snapshot.strategy_lifecycle_version,
            parameters=[
                OperationalPaperSessionProfileStrategyParameterResponse.from_domain(name, value)
                for name, value in snapshot.parameters
            ],
            parameters_checksum=snapshot.parameters_checksum,
            snapshot_checksum=snapshot.snapshot_checksum,
        )


class OperationalPaperSessionProfileSpecificationResponse(ApiSchema):
    schema_version: int
    name: str
    description: str
    mandate_binding: OperationalPaperSessionProfileMandateBindingResponse
    selected_instrument: OperationalPaperSessionProfileInstrumentResponse
    timeframe: str
    start_at: datetime
    warmup_candles: int
    strategy_snapshot: OperationalPaperSessionProfileStrategySnapshotResponse
    execution: OperationalPaperSessionProfileExecutionResponse
    instrument_constraints: OperationalPaperSessionProfileInstrumentConstraintsResponse
    risk_limits: OperationalPaperSessionProfileRiskLimitsResponse
    history_window: int
    max_candles: int
    max_orders: int
    max_events: int
    engine_version: str
    market_regime_policy: OperationalPaperSessionProfileMarketRegimeResponse | None

    @classmethod
    def from_domain(cls, specification: OperationalPaperSessionProfileSpecification) -> Self:
        return cls(
            schema_version=specification.schema_version,
            name=specification.name,
            description=specification.description,
            mandate_binding=(
                OperationalPaperSessionProfileMandateBindingResponse.from_domain(
                    specification.mandate_binding
                )
            ),
            selected_instrument=OperationalPaperSessionProfileInstrumentResponse.from_domain(
                specification.selected_instrument
            ),
            timeframe=specification.timeframe.code,
            start_at=specification.start_at,
            warmup_candles=specification.warmup_candles,
            strategy_snapshot=(
                OperationalPaperSessionProfileStrategySnapshotResponse.from_domain(
                    specification.strategy_snapshot
                )
            ),
            execution=OperationalPaperSessionProfileExecutionResponse.from_domain(
                specification.execution
            ),
            instrument_constraints=(
                OperationalPaperSessionProfileInstrumentConstraintsResponse.from_domain(
                    specification.instrument_constraints
                )
            ),
            risk_limits=OperationalPaperSessionProfileRiskLimitsResponse.from_domain(
                specification.risk_limits
            ),
            history_window=specification.history_window,
            max_candles=specification.max_candles,
            max_orders=specification.max_orders,
            max_events=specification.max_events,
            engine_version=specification.engine_version,
            market_regime_policy=(
                None
                if specification.market_regime_policy is None
                else OperationalPaperSessionProfileMarketRegimeResponse.from_domain(
                    specification.market_regime_policy
                )
            ),
        )


class OperationalPaperSessionProfileResponse(ApiSchema):
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

    @classmethod
    def from_domain(cls, profile: OperationalPaperSessionProfile) -> Self:
        return cls(
            profile_id=profile.profile_id,
            state=profile.state,
            current_revision=profile.current_revision,
            record_version=profile.record_version,
            approved_revision=profile.approved_revision,
            approved_checksum=profile.approved_checksum,
            created_by=profile.created_by,
            created_at=profile.created_at,
            approved_by=profile.approved_by,
            approved_at=profile.approved_at,
            archived_by=profile.archived_by,
            archived_at=profile.archived_at,
        )


class OperationalPaperSessionProfileRevisionResponse(ApiSchema):
    profile_id: UUID
    revision: int
    specification: OperationalPaperSessionProfileSpecificationResponse
    specification_checksum: str
    created_by: UUID
    created_at: datetime

    @classmethod
    def from_domain(cls, revision: OperationalPaperSessionProfileRevision) -> Self:
        return cls(
            profile_id=revision.profile_id,
            revision=revision.revision,
            specification=OperationalPaperSessionProfileSpecificationResponse.from_domain(
                revision.specification
            ),
            specification_checksum=revision.specification_checksum,
            created_by=revision.created_by,
            created_at=revision.created_at,
        )


class OperationalPaperSessionProfileCurrentResponse(ApiSchema):
    profile: OperationalPaperSessionProfileResponse
    revision: OperationalPaperSessionProfileRevisionResponse

    @classmethod
    def from_domain(
        cls,
        current: tuple[
            OperationalPaperSessionProfile,
            OperationalPaperSessionProfileRevision,
        ],
    ) -> Self:
        profile, revision = current
        return cls(
            profile=OperationalPaperSessionProfileResponse.from_domain(profile),
            revision=OperationalPaperSessionProfileRevisionResponse.from_domain(revision),
        )


class OperationalPaperSessionProfileListResponse(ApiSchema):
    items: list[OperationalPaperSessionProfileCurrentResponse]
    limit: int
    offset: int
    total: int

    @classmethod
    def from_domain(
        cls,
        items: list[
            tuple[
                OperationalPaperSessionProfile,
                OperationalPaperSessionProfileRevision,
            ]
        ],
        *,
        limit: int,
        offset: int,
        total: int,
    ) -> Self:
        return cls(
            items=[
                OperationalPaperSessionProfileCurrentResponse.from_domain(item) for item in items
            ],
            limit=limit,
            offset=offset,
            total=total,
        )


class OperationalPaperSessionProfileRevisionListResponse(ApiSchema):
    items: list[OperationalPaperSessionProfileRevisionResponse]
    limit: int
    offset: int
    total: int

    @classmethod
    def from_domain(
        cls,
        items: list[OperationalPaperSessionProfileRevision],
        *,
        limit: int,
        offset: int,
        total: int,
    ) -> Self:
        return cls(
            items=[
                OperationalPaperSessionProfileRevisionResponse.from_domain(item) for item in items
            ],
            limit=limit,
            offset=offset,
            total=total,
        )
