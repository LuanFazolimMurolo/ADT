"""Gate 2D integration across canonical paper artifacts and PostgreSQL settlement."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import UUID

import psycopg
import pytest

import tests.test_operational_paper_session_settlements_repository as settlement_fixtures
from app.database import Database
from app.market_data.domain import Candle, Exchange, MarketType
from app.operational_paper_capital_authorizations import (
    OperationalPaperCapitalAuthorizationSpecification,
    OperationalPaperCapitalAuthorizationState,
)
from app.operational_paper_session_materializations import (
    build_operational_paper_session_materialization_plan,
)
from app.operational_paper_session_profiles import (
    OperationalPaperSessionProfileCreateIntent,
)
from app.paper_trading.domain import (
    PaperSessionConfig,
    paper_config_checksum,
    paper_session_id,
)
from app.paper_trading.service import PaperTradingService
from app.repositories.operational_paper_capital_authorizations import (
    PostgresOperationalPaperCapitalAuthorizationRepository,
)
from app.repositories.operational_paper_session_profiles import (
    PostgresOperationalPaperSessionProfileRepository,
)
from app.repositories.operational_paper_session_runs import (
    PostgresOperationalPaperSessionRunRepository,
)
from app.repositories.operational_paper_session_settlements import (
    PostgresOperationalPaperSessionSettlementRepository,
)
from app.repositories.strategy_definitions import (
    PostgresStrategyDefinitionRepository,
)
from app.services.operational_paper_session_settlements import (
    OperationalPaperSessionSettlementService,
)
from app.strategies.definitions import (
    StrategyDefinition,
    StrategyDefinitionSpec,
    strategy_parameter_checksum,
)
from tests.test_operational_paper_session_profiles_repository import (
    _approved_mandate,
)
from tests.test_operational_paper_session_profiles_repository import (
    _intent as _profile_intent,
)
from tests.test_paper_trading import FakeSource


async def _builtin_sources(
    database: Database,
    actor_id: UUID,
) -> tuple[OperationalPaperSessionProfileCreateIntent, StrategyDefinition]:
    """Create the normal profile authority using an actual runtime builtin."""

    mandate_id, mandate_checksum = await _approved_mandate(
        database,
        actor_id,
    )

    parameters = ()
    definition = await PostgresStrategyDefinitionRepository(database).create(
        StrategyDefinitionSpec(
            display_name="Gate 2D builtin no-op",
            plugin_name="no-op",
            plugin_version="2",
            plugin_schema_version=1,
            lifecycle_version=2,
            parameters=parameters,
            parameters_checksum=strategy_parameter_checksum(parameters),
        ),
        actor_id=actor_id,
    )

    return (
        _profile_intent(
            mandate_id,
            mandate_checksum,
            definition,
        ),
        definition,
    )


def _flat_candles(config: PaperSessionConfig) -> tuple[Candle, ...]:
    """Build sufficient constant-price candles for a neutral no-op replay."""

    timeframe = config.timeframe
    pair = config.pair

    minimum_count = config.warmup_candles + config.history_window + 3
    count = min(config.max_candles, max(minimum_count, 3))
    assert count > config.warmup_candles

    price = Decimal("100")

    return tuple(
        Candle(
            exchange=Exchange.BINANCE,
            market_type=MarketType.SPOT,
            symbol=f"{pair.base}/{pair.quote}",
            timeframe=timeframe,
            open_time=config.context_start + index * timeframe.duration,
            close_time=config.context_start + (index + 1) * timeframe.duration,
            open=price,
            high=price,
            low=price,
            close=price,
            volume=Decimal("10"),
            quote_volume=Decimal("1000"),
            trade_count=10,
            is_closed=True,
            source="gate-2d-integration",
        )
        for index in range(count)
    )


@pytest.mark.asyncio
async def test_verified_local_artifacts_settle_terminal_epoch_atomically(
    tmp_path,
    database_url,
    database,
    auth_user_id,
    monkeypatch,
) -> None:
    # The generic repository fixture intentionally uses a fake strategy identity
    # because Gate 2C never executes paper trading. Gate 2D does execute/replay
    # it, so preserve the exact fixture chain while sourcing a real builtin.
    monkeypatch.setattr(
        settlement_fixtures,
        "_sources",
        _builtin_sources,
    )

    context = await settlement_fixtures._context(
        database_url,
        database,
        auth_user_id,
    )
    epoch = context.epoch
    authorization = context.authorization

    assert epoch.terminal_at is not None
    assert authorization.state is OperationalPaperCapitalAuthorizationState.AUTHORIZED

    profile_binding = authorization.profile_binding
    profile_repository = PostgresOperationalPaperSessionProfileRepository(database)
    profile_revision = await profile_repository.get_revision(
        profile_binding.profile_id,
        profile_binding.approved_revision,
    )

    assert profile_revision is not None
    assert profile_revision.specification_checksum == profile_binding.specification_checksum

    snapshot = profile_revision.specification.strategy_snapshot
    assert snapshot.plugin_name == "no-op"
    assert snapshot.plugin_version == "2"
    assert snapshot.strategy_lifecycle_version == 2
    assert snapshot.parameters == ()

    authorization_specification = OperationalPaperCapitalAuthorizationSpecification(
        schema_version=authorization.schema_version,
        profile_binding=authorization.profile_binding,
        simulation_id=authorization.simulation_id,
        quote_asset=authorization.quote_asset,
        authorized_capital=authorization.authorized_capital,
    )

    plan = build_operational_paper_session_materialization_plan(
        authorization_id=authorization.authorization_id,
        authorization_specification=authorization_specification,
        authorization_checksum=authorization.authorization_checksum,
        profile_revision=profile_revision,
    )
    config = plan.config

    # Frozen PostgreSQL administrative evidence must reconstruct the exact local
    # identity already bound to the terminal operational epoch.
    assert paper_session_id(config) == epoch.session_id
    assert paper_config_checksum(config) == epoch.config_checksum
    assert config.initial_capital == authorization.authorized_capital
    assert config.strategy.name == "no-op"
    assert config.strategy.version == "2"

    source = FakeSource(_flat_candles(config))
    paper_service = PaperTradingService(
        tmp_path,
        source=source,
    )

    created = paper_service.create(config)
    assert created == config

    run_result = paper_service.run_once(epoch.session_id)
    state = run_result.state

    assert state.session_id == epoch.session_id
    assert state.config_checksum == epoch.config_checksum

    # no-op@2 cannot create economic exposure. This gives Gate 2D a genuine
    # terminal-flat filesystem artifact without manufacturing PortfolioSnapshot.
    assert state.portfolio.base_quantity == Decimal("0")
    assert state.portfolio.average_entry_price == Decimal("0")
    assert state.portfolio.cost_basis == Decimal("0")
    assert state.portfolio.unrealized_pnl == Decimal("0")
    assert state.portfolio.realized_pnl == Decimal("0")
    assert state.portfolio.quote_cash == config.initial_capital
    assert state.portfolio.total_fees == Decimal("0")
    assert state.portfolio.total_slippage_cost == Decimal("0")

    (
        verified_config,
        verified_state,
        verified_binding,
    ) = paper_service.verify_settlement_evidence(epoch.session_id)

    assert verified_config == config
    assert verified_state == state
    assert verified_binding.session_id == epoch.session_id
    assert verified_binding.config_checksum == epoch.config_checksum
    assert verified_binding.state_id == state.state_id
    assert verified_binding.state_checksum == state.checksum
    assert verified_binding.dataset_version == state.dataset_version
    assert verified_binding.source_checksum == state.source_checksum

    settlement_repository = PostgresOperationalPaperSessionSettlementRepository(database)
    service = OperationalPaperSessionSettlementService(
        repository=settlement_repository,
        run_repository=PostgresOperationalPaperSessionRunRepository(database),
        paper_service=paper_service,
        clock=lambda: epoch.terminal_at + timedelta(seconds=1),
    )

    settlement = await service.settle(
        settlement_fixtures._intent(epoch),
        actor_id=auth_user_id,
        idempotency_key="gate-2d-integration:settle",
    )

    specification = settlement.specification

    assert specification.era_id == context.era.era_id
    assert specification.era_checksum == context.era.era_checksum
    assert specification.simulation_id == epoch.simulation_id
    assert specification.epoch_id == epoch.epoch_id
    assert specification.epoch_checksum == epoch.epoch_checksum

    assert specification.session_id == verified_binding.session_id
    assert specification.config_checksum == verified_binding.config_checksum
    assert specification.state_id == verified_binding.state_id
    assert specification.state_checksum == verified_binding.state_checksum
    assert specification.dataset_version == verified_binding.dataset_version
    assert specification.source_checksum == verified_binding.source_checksum
    assert specification.timeline_id == verified_binding.timeline_id
    assert specification.timeline_content_checksum == verified_binding.timeline_content_checksum

    assert specification.initial_capital == config.initial_capital
    assert specification.final_quote_cash == config.initial_capital
    assert specification.base_quantity == Decimal("0")
    assert specification.average_entry_price == Decimal("0")
    assert specification.cost_basis == Decimal("0")
    assert specification.unrealized_pnl == Decimal("0")
    assert specification.realized_pnl == Decimal("0")
    assert specification.total_fees == Decimal("0")
    assert specification.total_slippage_cost == Decimal("0")
    assert specification.settlement_delta == Decimal("0")
    assert specification.ledger_movement_id is None

    persisted = await settlement_repository.get_by_session(epoch.session_id)
    assert persisted == settlement

    authorization_after = await (
        PostgresOperationalPaperCapitalAuthorizationRepository(database)
    ).get(authorization.authorization_id)

    assert authorization_after is not None
    assert authorization_after.state is OperationalPaperCapitalAuthorizationState.REVOKED
    assert authorization_after.record_version == authorization.record_version + 1
    assert authorization_after.revoked_by == auth_user_id
    assert authorization_after.revoked_at == settlement.settled_at

    # Zero delta consumes the reservation but must create no settlement PnL
    # movement. The context seeded only the authoritative INITIAL_CAPITAL row.
    with psycopg.connect(database_url) as connection:
        movements = connection.execute(
            """
            select type, amount
            from public.capital_movements
            where simulation_id = %s
            order by created_at, id
            """,
            (epoch.simulation_id,),
        ).fetchall()

    assert len(movements) == 1
    assert movements[0][0] == "INITIAL_CAPITAL"
    assert Decimal(movements[0][1]) == Decimal("100")

    # Exact committed replay is PostgreSQL historical authority and therefore
    # must not double-consume the authorization or append ledger evidence.
    replay = await service.settle(
        settlement_fixtures._intent(epoch),
        actor_id=auth_user_id,
        idempotency_key="gate-2d-integration:settle",
    )
    assert replay == settlement

    authorization_replay = await (
        PostgresOperationalPaperCapitalAuthorizationRepository(database)
    ).get(authorization.authorization_id)
    assert authorization_replay == authorization_after

    with psycopg.connect(database_url) as connection:
        movement_count = connection.execute(
            """
            select count(*)
            from public.capital_movements
            where simulation_id = %s
            """,
            (epoch.simulation_id,),
        ).fetchone()

    assert movement_count is not None
    assert movement_count[0] == 1
