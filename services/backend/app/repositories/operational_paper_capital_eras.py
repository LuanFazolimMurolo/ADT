"""Transactional official-era designation over the existing simulation authority."""

from collections.abc import Mapping
from dataclasses import fields, replace
from datetime import datetime
from typing import NoReturn
from uuid import UUID, uuid4

from psycopg import Error
from psycopg.errors import UniqueViolation

import app.operational_paper_capital_eras as eras
from app.database.errors import raise_domain_error
from app.database.pool import Database, DatabaseConnection
from app.domain.errors import DomainError, PersistenceError
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

_TABLE = "operational_paper_capital_eras"


def operational_paper_capital_era_from_row(
    row: Mapping[str, object],
) -> eras.OperationalPaperCapitalEra:
    """Rebuild checksums and provenance, normalizing only stored timestamptz offsets."""
    try:
        era = eras.OperationalPaperCapitalEra(
            era_id=uuid_value(row["era_id"]),
            schema_version=integer_value(row["schema_version"]),
            designation_contract_version=integer_value(row["designation_contract_version"]),
            simulation_id=uuid_value(row["simulation_id"]),
            currency=text_value(row["currency"]),
            initial_capital=decimal_value(row["initial_capital"]),
            simulation_started_at=timestamp_value(row["simulation_started_at"]),
            era_checksum=text_value(row["era_checksum"]),
            designated_by=uuid_value(row["designated_by"]),
            designated_at=timestamp_value(row["designated_at"]),
            designation_idempotency_key=text_value(row["designation_idempotency_key"]),
            designation_intent_fingerprint=text_value(row["designation_intent_fingerprint"]),
        )
        expected = eras.operational_paper_capital_era_designation_intent_fingerprint(
            eras.OperationalPaperCapitalEraDesignationIntent(era.simulation_id)
        )
        if era.currency != row["currency"] or era.designation_intent_fingerprint != expected:
            raise ValueError
        return era
    except (DomainError, KeyError, TypeError, ValueError) as error:
        raise PersistenceError() from error


def _raise_database_error(error: Error) -> NoReturn:
    if error.diag.constraint_name == "op_pc_era_actor_idempotency_key":
        raise eras.OperationalPaperCapitalEraIdempotencyConflictError() from error
    if error.diag.constraint_name == "op_pc_era_simulation_key":
        raise eras.OperationalPaperCapitalEraSimulationAlreadyDesignatedError() from error
    if error.sqlstate in {"40001", "40P01"}:
        raise eras.OperationalPaperCapitalEraEligibilityConflictError() from error
    raise_domain_error(error)


async def _replay(
    connection: DatabaseConnection,
    actor: UUID,
    key: str,
    fingerprint: str,
) -> eras.OperationalPaperCapitalEra | None:
    cursor = await connection.execute(
        "select * from public.operational_paper_capital_eras "
        "where designated_by = %s and designation_idempotency_key = %s",
        (actor, key),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    era = operational_paper_capital_era_from_row(row)
    if era.designation_intent_fingerprint != fingerprint:
        raise eras.OperationalPaperCapitalEraIdempotencyConflictError()
    return era


class PostgresOperationalPaperCapitalEraRepository:
    """Own each bounded database transaction; never mutate the financial ledger."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get(self, era_id: UUID) -> eras.OperationalPaperCapitalEra | None:
        return await self._get("era_id", era_id)

    async def get_by_simulation(
        self, simulation_id: UUID
    ) -> eras.OperationalPaperCapitalEra | None:
        return await self._get("simulation_id", simulation_id)

    async def _get(self, column: str, identity: UUID) -> eras.OperationalPaperCapitalEra | None:
        try:
            identity = uuid_value(identity)
        except ValueError:
            raise eras.InvalidOperationalPaperCapitalEraSpecificationError() from None
        # column comes exclusively from the two fixed public methods above.
        try:
            async with self._database.transaction() as connection:
                cursor = await connection.execute(
                    f"select * from public.operational_paper_capital_eras where {column} = %s",
                    (identity,),
                )
                row = await cursor.fetchone()
                return None if row is None else operational_paper_capital_era_from_row(row)
        except Error as error:
            _raise_database_error(error)

    async def designate(
        self,
        intent: eras.OperationalPaperCapitalEraDesignationIntent,
        *,
        actor_id: UUID,
        idempotency_key: str,
        now: datetime,
    ) -> eras.OperationalPaperCapitalEra:
        if not isinstance(intent, eras.OperationalPaperCapitalEraDesignationIntent):
            raise eras.InvalidOperationalPaperCapitalEraSpecificationError()
        intent = replace(intent)
        try:
            actor_id, now = uuid_value(actor_id), utc_now(now)
        except ValueError:
            raise eras.InvalidOperationalPaperCapitalEraSpecificationError() from None
        key = eras.validate_operational_paper_capital_era_idempotency_key(idempotency_key)
        fingerprint = eras.operational_paper_capital_era_designation_intent_fingerprint(intent)
        try:
            async with self._database.transaction() as connection:
                replay = await _replay(connection, actor_id, key, fingerprint)
                if replay is not None:
                    return replay
                simulation = await lock_simulation(connection, intent.simulation_id)
                # A waiter must observe the winner after acquiring the mutex.
                replay = await _replay(connection, actor_id, key, fingerprint)
                if replay is not None:
                    return replay
                cursor = await connection.execute(
                    "select era_id from public.operational_paper_capital_eras "
                    "where simulation_id=%s",
                    (intent.simulation_id,),
                )
                if await cursor.fetchone() is not None:
                    raise eras.OperationalPaperCapitalEraSimulationAlreadyDesignatedError()
                if simulation is None or simulation["status"] != "ACTIVE":
                    raise eras.OperationalPaperCapitalEraEligibilityConflictError()
                # Phase 1's simulation_runs_single_active_uidx already excludes
                # two ACTIVE simulations. Holding this ACTIVE row prevents its
                # terminalization/replacement during designation; no global mutex.
                cursor = await connection.execute(
                    "select era.era_id from public.operational_paper_capital_eras era "
                    "join public.simulation_runs sim on sim.id=era.simulation_id "
                    "where sim.status='ACTIVE' and sim.id<>%s limit 1",
                    (intent.simulation_id,),
                )
                if await cursor.fetchone() is not None:
                    raise eras.OperationalPaperCapitalEraActiveConflictError()
                await self._require_unused(connection, intent.simulation_id, simulation)
                try:
                    specification = eras.build_operational_paper_capital_era_specification(
                        intent,
                        currency=simulation["currency"],
                        initial_capital=simulation["initial_capital"],
                        simulation_started_at=timestamp_value(simulation["started_at"]),
                    )
                    if (
                        specification.currency != simulation["currency"]
                        or now < specification.simulation_started_at
                    ):
                        raise ValueError
                except (DomainError, ValueError):
                    raise eras.OperationalPaperCapitalEraEligibilityConflictError() from None
                era = eras.OperationalPaperCapitalEra(
                    era_id=uuid4(),
                    **{
                        field.name: getattr(specification, field.name)
                        for field in fields(specification)
                    },
                    era_checksum=eras.operational_paper_capital_era_specification_checksum(
                        specification
                    ),
                    designated_by=actor_id,
                    designated_at=now,
                    designation_idempotency_key=key,
                    designation_intent_fingerprint=fingerprint,
                )
                row = await insert_evidence(
                    connection, _TABLE, {f.name: getattr(era, f.name) for f in fields(era)}
                )
                return operational_paper_capital_era_from_row(row)
        except UniqueViolation as error:
            # Resolve only after rollback, including races across different simulations.
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

    @staticmethod
    async def _require_unused(
        connection: DatabaseConnection,
        simulation_id: UUID,
        simulation: Mapping[str, object],
    ) -> None:
        cursor = await connection.execute(
            "select type, amount from public.capital_movements where simulation_id=%s limit 2",
            (simulation_id,),
        )
        movements = await cursor.fetchall()
        if (
            len(movements) != 1
            or movements[0]["type"] != "INITIAL_CAPITAL"
            or movements[0]["amount"] != simulation["initial_capital"]
        ):
            raise eras.OperationalPaperCapitalEraEligibilityConflictError()
        cursor = await connection.execute(
            """
            select exists(select 1 from public.operational_paper_capital_authorizations
                          where simulation_id=%s)
                or exists(select 1 from public.operational_paper_session_materializations
                          where simulation_id=%s)
                or exists(select 1 from public.operational_paper_session_activations
                          where simulation_id=%s)
                or exists(select 1 from public.operational_paper_session_run_epochs
                          where simulation_id=%s) as used
            """,
            (simulation_id,) * 4,
        )
        row = await cursor.fetchone()
        if row is None or row["used"] is not False:
            raise eras.OperationalPaperCapitalEraEligibilityConflictError()
