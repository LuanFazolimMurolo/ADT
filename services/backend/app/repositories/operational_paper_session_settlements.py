"""Atomic terminal settlement over the existing Phase 1 capital ledger."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import fields, replace
from datetime import datetime
from decimal import Decimal
from typing import NoReturn
from uuid import UUID, uuid4

from psycopg import Error
from psycopg.errors import UniqueViolation

import app.operational_paper_capital_authorizations as authorizations
import app.operational_paper_capital_eras as eras
import app.operational_paper_session_runs as runs
import app.operational_paper_session_settlements as settlements
from app.backtesting.domain import PortfolioSnapshot
from app.database.errors import raise_domain_error
from app.database.pool import Database, DatabaseConnection
from app.domain.errors import DomainError, PersistenceError
from app.paper_trading.persisted_state import PaperPersistedStateBinding
from app.repositories._operational_paper_capital import (
    decimal_value,
    insert_evidence,
    integer_value,
    lock_simulation,
    text_value,
    timestamp_value,
    utc_now,
    uuid_value,
)
from app.repositories.operational_paper_capital_authorizations import (
    operational_paper_capital_authorization_from_row,
)
from app.repositories.operational_paper_capital_eras import (
    operational_paper_capital_era_from_row,
)
from app.repositories.operational_paper_session_runs import (
    operational_paper_session_run_epoch_from_row,
)

_TABLE = "operational_paper_session_settlements"
_SHA256 = re.compile(r"[0-9a-f]{64}")


def operational_paper_session_settlement_from_row(
    row: Mapping[str, object],
) -> settlements.OperationalPaperSessionSettlement:
    """Hydrate immutable evidence and revalidate its frozen domain checksum."""
    try:
        specification = settlements.OperationalPaperSessionSettlementSpecification(
            schema_version=integer_value(row["schema_version"]),
            settlement_contract_version=integer_value(row["settlement_contract_version"]),
            era_id=uuid_value(row["era_id"]),
            era_checksum=text_value(row["era_checksum"]),
            simulation_id=uuid_value(row["simulation_id"]),
            epoch_id=uuid_value(row["epoch_id"]),
            epoch_checksum=text_value(row["epoch_checksum"]),
            epoch_terminal_at=timestamp_value(row["epoch_terminal_at"]),
            authorization_id=uuid_value(row["authorization_id"]),
            authorization_checksum=text_value(row["authorization_checksum"]),
            session_id=text_value(row["session_id"]),
            config_checksum=text_value(row["config_checksum"]),
            state_id=text_value(row["state_id"]),
            state_checksum=text_value(row["state_checksum"]),
            dataset_version=text_value(row["dataset_version"]),
            source_checksum=text_value(row["source_checksum"]),
            timeline_id=text_value(row["timeline_id"]),
            timeline_content_checksum=text_value(row["timeline_content_checksum"]),
            initial_capital=decimal_value(row["initial_capital"]),
            final_quote_cash=decimal_value(row["final_quote_cash"]),
            realized_pnl=decimal_value(row["realized_pnl"]),
            unrealized_pnl=decimal_value(row["unrealized_pnl"]),
            base_quantity=decimal_value(row["base_quantity"]),
            average_entry_price=decimal_value(row["average_entry_price"]),
            cost_basis=decimal_value(row["cost_basis"]),
            total_fees=decimal_value(row["total_fees"]),
            total_slippage_cost=decimal_value(row["total_slippage_cost"]),
            settlement_delta=decimal_value(row["settlement_delta"]),
            ledger_movement_id=(
                None if row["ledger_movement_id"] is None else uuid_value(row["ledger_movement_id"])
            ),
        )
        settlement = settlements.OperationalPaperSessionSettlement(
            settlement_id=uuid_value(row["settlement_id"]),
            specification=specification,
            settlement_checksum=text_value(row["settlement_checksum"]),
            settled_by=uuid_value(row["settled_by"]),
            settled_at=timestamp_value(row["settled_at"]),
            settle_idempotency_key=text_value(row["settle_idempotency_key"]),
            settle_intent_fingerprint=text_value(row["settle_intent_fingerprint"]),
        )
        expected_fingerprint = settlements.operational_paper_session_settlement_intent_fingerprint(
            settlements.OperationalPaperSessionSettlementIntent(
                specification.epoch_id,
                specification.epoch_checksum,
            )
        )
        if settlement.settle_intent_fingerprint != expected_fingerprint:
            raise ValueError
        return settlement
    except (DomainError, KeyError, TypeError, ValueError) as error:
        raise PersistenceError() from error


def _raise_database_error(error: Error) -> NoReturn:
    constraint = error.diag.constraint_name
    message = error.diag.message_primary or ""

    if constraint == "op_ps_settlement_actor_idempotency_key":
        raise settlements.OperationalPaperSessionSettlementIdempotencyConflictError() from error

    if constraint == "op_ps_settlement_session_key":
        raise settlements.OperationalPaperSessionAlreadySettledError() from error

    if constraint == "op_ps_settlement_movement_key":
        raise settlements.OperationalPaperSessionAlreadySettledError() from error

    if error.sqlstate in {"40001", "40P01"}:
        raise settlements.OperationalPaperSessionSettlementEligibilityConflictError() from error

    if message.startswith("operational_paper_capital_authorization_"):
        raise settlements.OperationalPaperSessionSettlementEligibilityConflictError() from error

    if message == "capital_movement_would_violate_authorized_reservations":
        raise settlements.OperationalPaperSessionSettlementEligibilityConflictError() from error

    if message == "The capital movement would make the simulation balance negative.":
        raise settlements.OperationalPaperSessionSettlementEligibilityConflictError() from error

    raise_domain_error(error)


async def _replay(
    connection: DatabaseConnection,
    actor_id: UUID,
    key: str,
    fingerprint: str,
) -> settlements.OperationalPaperSessionSettlement | None:
    cursor = await connection.execute(
        "select * from public.operational_paper_session_settlements "
        "where settled_by = %s and settle_idempotency_key = %s",
        (actor_id, key),
    )
    row = await cursor.fetchone()
    if row is None:
        return None

    settlement = operational_paper_session_settlement_from_row(row)
    if settlement.settle_intent_fingerprint != fingerprint:
        raise settlements.OperationalPaperSessionSettlementIdempotencyConflictError()
    return settlement


async def _epoch_simulation_id(
    connection: DatabaseConnection,
    epoch_id: UUID,
) -> UUID:
    cursor = await connection.execute(
        "select simulation_id from public.operational_paper_session_run_epochs where epoch_id = %s",
        (epoch_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise settlements.OperationalPaperSessionSettlementEligibilityConflictError()
    try:
        return uuid_value(row["simulation_id"])
    except ValueError:
        raise PersistenceError() from None


async def _locked_epoch(
    connection: DatabaseConnection,
    epoch_id: UUID,
) -> runs.OperationalPaperSessionRunEpoch:
    cursor = await connection.execute(
        "select * from public.operational_paper_session_run_epochs where epoch_id = %s for update",
        (epoch_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise settlements.OperationalPaperSessionSettlementEligibilityConflictError()
    return operational_paper_session_run_epoch_from_row(row)


async def _era_for_simulation(
    connection: DatabaseConnection,
    simulation_id: UUID,
) -> eras.OperationalPaperCapitalEra:
    cursor = await connection.execute(
        "select * from public.operational_paper_capital_eras where simulation_id = %s",
        (simulation_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise settlements.OperationalPaperSessionSettlementEligibilityConflictError()
    return operational_paper_capital_era_from_row(row)


async def _locked_authorization(
    connection: DatabaseConnection,
    authorization_id: UUID,
) -> authorizations.OperationalPaperCapitalAuthorization:
    cursor = await connection.execute(
        "select * "
        "from public.operational_paper_capital_authorizations "
        "where authorization_id = %s "
        "for update",
        (authorization_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise settlements.OperationalPaperSessionSettlementEligibilityConflictError()
    return operational_paper_capital_authorization_from_row(row)


async def _consume_authorization(
    connection: DatabaseConnection,
    authorization: authorizations.OperationalPaperCapitalAuthorization,
    *,
    actor_id: UUID,
    now: datetime,
) -> None:
    """Release exactly the reservation used by the terminal settlement."""
    cursor = await connection.execute(
        """
        update public.operational_paper_capital_authorizations
        set state = 'REVOKED',
            record_version = record_version + 1,
            revoked_by = %s,
            revoked_at = %s
        where authorization_id = %s
          and simulation_id = %s
          and state = 'AUTHORIZED'
          and record_version = %s
        returning authorization_id
        """,
        (
            actor_id,
            now,
            authorization.authorization_id,
            authorization.simulation_id,
            authorization.record_version,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise settlements.OperationalPaperSessionSettlementEligibilityConflictError()


async def _insert_movement(
    connection: DatabaseConnection,
    *,
    movement_id: UUID,
    simulation_id: UUID,
    delta: Decimal,
    actor_id: UUID,
) -> None:
    if delta == 0:
        raise PersistenceError()

    movement_type = "TRADE_PROFIT" if delta > 0 else "TRADE_LOSS"
    cursor = await connection.execute(
        """
        insert into public.capital_movements (
            id,
            simulation_id,
            type,
            amount,
            reason,
            created_by
        )
        values (%s, %s, %s, %s, %s, %s)
        returning id, simulation_id, type, amount
        """,
        (
            movement_id,
            simulation_id,
            movement_type,
            delta,
            "Operational paper session settlement",
            actor_id,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise PersistenceError()

    if (
        row["id"] != movement_id
        or row["simulation_id"] != simulation_id
        or row["type"] != movement_type
        or row["amount"] != delta
    ):
        raise PersistenceError()


def _settlement_values(
    settlement: settlements.OperationalPaperSessionSettlement,
) -> dict[str, object]:
    specification = settlement.specification
    values = {
        field.name: getattr(specification, field.name)
        for field in fields(settlements.OperationalPaperSessionSettlementSpecification)
    }
    values.update(
        settlement_id=settlement.settlement_id,
        settlement_checksum=settlement.settlement_checksum,
        settled_by=settlement.settled_by,
        settled_at=settlement.settled_at,
        settle_idempotency_key=settlement.settle_idempotency_key,
        settle_intent_fingerprint=settlement.settle_intent_fingerprint,
    )
    return values


class PostgresOperationalPaperSessionSettlementRepository:
    """Atomically release reservation, post PnL and persist terminal evidence."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(
        self,
        settlement_id: UUID,
    ) -> settlements.OperationalPaperSessionSettlement | None:
        try:
            settlement_id = uuid_value(settlement_id)
        except ValueError:
            raise settlements.InvalidOperationalPaperSessionSettlementSpecificationError() from None

        try:
            async with self._database.transaction() as connection:
                cursor = await connection.execute(
                    "select * "
                    "from public.operational_paper_session_settlements "
                    "where settlement_id = %s",
                    (settlement_id,),
                )
                row = await cursor.fetchone()
                return None if row is None else operational_paper_session_settlement_from_row(row)
        except Error as error:
            _raise_database_error(error)

    async def get_by_session(
        self,
        session_id: str,
    ) -> settlements.OperationalPaperSessionSettlement | None:
        if not isinstance(session_id, str) or _SHA256.fullmatch(session_id) is None:
            raise settlements.InvalidOperationalPaperSessionSettlementSpecificationError()

        try:
            async with self._database.transaction() as connection:
                cursor = await connection.execute(
                    "select * "
                    "from public.operational_paper_session_settlements "
                    "where session_id = %s",
                    (session_id,),
                )
                row = await cursor.fetchone()
                return None if row is None else operational_paper_session_settlement_from_row(row)
        except Error as error:
            _raise_database_error(error)

    async def settle(
        self,
        intent: settlements.OperationalPaperSessionSettlementIntent,
        *,
        persisted_state_binding: PaperPersistedStateBinding,
        initial_capital: Decimal,
        portfolio: PortfolioSnapshot,
        actor_id: UUID,
        idempotency_key: str,
        now: datetime,
    ) -> settlements.OperationalPaperSessionSettlement:
        if not isinstance(intent, settlements.OperationalPaperSessionSettlementIntent):
            raise settlements.InvalidOperationalPaperSessionSettlementSpecificationError()

        try:
            intent = replace(intent)
            actor_id = uuid_value(actor_id)
            now = utc_now(now)
            persisted_state_binding = replace(persisted_state_binding)
            portfolio = replace(portfolio)
            initial_capital = decimal_value(initial_capital)
        except (TypeError, ValueError):
            raise settlements.InvalidOperationalPaperSessionSettlementSpecificationError() from None

        key = settlements.validate_operational_paper_session_settlement_idempotency_key(
            idempotency_key
        )
        fingerprint = settlements.operational_paper_session_settlement_intent_fingerprint(intent)

        try:
            async with self._database.transaction() as connection:
                replay = await _replay(connection, actor_id, key, fingerprint)
                if replay is not None:
                    return replay

                simulation_id = await _epoch_simulation_id(connection, intent.epoch_id)
                simulation = await lock_simulation(connection, simulation_id)
                if simulation is None or simulation["status"] != "ACTIVE":
                    raise settlements.OperationalPaperSessionSettlementEligibilityConflictError()

                # A waiter must observe a committed winner after acquiring the
                # simulation financial mutex.
                replay = await _replay(connection, actor_id, key, fingerprint)
                if replay is not None:
                    return replay

                epoch = await _locked_epoch(connection, intent.epoch_id)

                if (
                    epoch.epoch_checksum != intent.epoch_checksum
                    or epoch.simulation_id != simulation_id
                ):
                    raise settlements.OperationalPaperSessionSettlementEligibilityConflictError()

                existing_cursor = await connection.execute(
                    "select settlement_id "
                    "from public.operational_paper_session_settlements "
                    "where session_id = %s",
                    (epoch.session_id,),
                )
                if await existing_cursor.fetchone() is not None:
                    raise settlements.OperationalPaperSessionAlreadySettledError()

                era = await _era_for_simulation(connection, simulation_id)
                authorization = await _locked_authorization(
                    connection,
                    epoch.authorization_binding.authorization_id,
                )

                if (
                    authorization.state
                    is not authorizations.OperationalPaperCapitalAuthorizationState.AUTHORIZED
                ):
                    raise settlements.OperationalPaperSessionSettlementEligibilityConflictError()

                raw_delta = portfolio.quote_cash - initial_capital
                movement_id = None if raw_delta == 0 else uuid4()

                specification = (
                    settlements.build_operational_paper_session_settlement_specification(
                        intent,
                        era=era,
                        epoch=epoch,
                        authorization=authorization,
                        persisted_state_binding=persisted_state_binding,
                        initial_capital=initial_capital,
                        portfolio=portfolio,
                        ledger_movement_id=movement_id,
                    )
                )

                settlement = settlements.OperationalPaperSessionSettlement(
                    settlement_id=uuid4(),
                    specification=specification,
                    settlement_checksum=(
                        settlements.operational_paper_session_settlement_specification_checksum(
                            specification
                        )
                    ),
                    settled_by=actor_id,
                    settled_at=now,
                    settle_idempotency_key=key,
                    settle_intent_fingerprint=fingerprint,
                )

                # Consume/release the exact AUTHORIZED reservation before the
                # ledger insert. Both mutations remain under the already-held
                # simulation row mutex and in this same database transaction.
                await _consume_authorization(
                    connection,
                    authorization,
                    actor_id=actor_id,
                    now=now,
                )

                if specification.settlement_delta != 0:
                    if movement_id is None:
                        raise PersistenceError()
                    await _insert_movement(
                        connection,
                        movement_id=movement_id,
                        simulation_id=simulation_id,
                        delta=specification.settlement_delta,
                        actor_id=actor_id,
                    )

                row = await insert_evidence(
                    connection,
                    _TABLE,
                    _settlement_values(settlement),
                )
                return operational_paper_session_settlement_from_row(row)

        except UniqueViolation as error:
            # Unique races are resolved only after the failed transaction has
            # rolled back, so replay never runs inside an aborted transaction.
            try:
                async with self._database.transaction() as connection:
                    replay = await _replay(connection, actor_id, key, fingerprint)
                    if replay is not None:
                        return replay
            except Error as recovery_error:
                _raise_database_error(recovery_error)
            _raise_database_error(error)
        except Error as error:
            _raise_database_error(error)
