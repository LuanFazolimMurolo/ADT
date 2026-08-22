"""Repository contract tests against disposable local PostgreSQL."""

from __future__ import annotations

import asyncio
from dataclasses import fields
from datetime import UTC, datetime, timedelta, timezone
from typing import cast
from uuid import UUID, uuid4

import psycopg
import pytest

import app.operational_mandates.domain as mandate_domain
import app.repositories.operational_mandates as repository_module
from app.database import Database
from app.domain.errors import PersistenceError
from app.market_data.domain import Exchange, MarketType, TradingPair
from app.operational_mandates import (
    InvalidOperationalMandateSpecificationError,
    OperationalMandate,
    OperationalMandateChecksumMismatchError,
    OperationalMandateIdempotencyConflictError,
    OperationalMandateInstrument,
    OperationalMandateNotFoundError,
    OperationalMandateRecordVersionConflictError,
    OperationalMandateRevision,
    OperationalMandateRevisionConflictError,
    OperationalMandateSpecification,
    OperationalMandateState,
    OperationalMandateStateTransitionConflictError,
    operational_mandate_create_request_fingerprint,
    operational_mandate_specification_checksum,
)
from app.repositories.operational_mandates import (
    PostgresOperationalMandateRepository,
)
from tests.postgres_support import add_auth_user

BASE_TIME = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


def _instrument(base: str, quote: str = "USDT") -> OperationalMandateInstrument:
    return OperationalMandateInstrument(
        exchange=Exchange.BINANCE,
        market_type=MarketType.SPOT,
        pair=TradingPair(base, quote),
    )


def _specification(
    *,
    name: str = "Primary mandate",
    description: str = "Bounded Binance Spot authority",
    instruments: tuple[OperationalMandateInstrument, ...] | None = None,
) -> OperationalMandateSpecification:
    return OperationalMandateSpecification(
        schema_version=1,
        name=name,
        description=description,
        instruments=instruments or (_instrument("BTC"),),
    )


async def _create(
    repository: PostgresOperationalMandateRepository,
    actor_id: UUID,
    *,
    specification: OperationalMandateSpecification | None = None,
    idempotency_key: str = "create-1",
    now: datetime = BASE_TIME,
) -> tuple[OperationalMandate, OperationalMandateRevision]:
    return await repository.create(
        specification or _specification(),
        actor_id=actor_id,
        idempotency_key=idempotency_key,
        now=now,
    )


async def _counts(database: Database, mandate_id: UUID) -> tuple[int, int]:
    async with database.transaction() as connection:
        revision_cursor = await connection.execute(
            """
            select count(*) as total
            from public.operational_mandate_revisions
            where mandate_id = %s
            """,
            (mandate_id,),
        )
        revision_row = await revision_cursor.fetchone()
        instrument_cursor = await connection.execute(
            """
            select count(*) as total
            from public.operational_mandate_revision_instruments
            where mandate_id = %s
            """,
            (mandate_id,),
        )
        instrument_row = await instrument_cursor.fetchone()
    assert revision_row is not None
    assert instrument_row is not None
    return int(revision_row["total"]), int(instrument_row["total"])


async def _total_rows(database: Database) -> tuple[int, int, int]:
    async with database.transaction() as connection:
        totals: list[int] = []
        for table in (
            "operational_mandates",
            "operational_mandate_revisions",
            "operational_mandate_revision_instruments",
        ):
            cursor = await connection.execute(f"select count(*) as total from public.{table}")
            row = await cursor.fetchone()
            assert row is not None
            totals.append(int(row["total"]))
    return totals[0], totals[1], totals[2]


def _add_actor(database_url: str) -> UUID:
    actor_id = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        add_auth_user(connection, actor_id)
    return actor_id


class _DatabaseMustNotBeAccessed:
    def transaction(self) -> object:
        raise AssertionError("invalid mandate_id reached persistence")


@pytest.mark.parametrize(
    "invalid_mandate_id",
    [None, "550e8400-e29b-41d4-a716-446655440000", 123, object()],
)
@pytest.mark.parametrize(
    "method_name",
    ["get", "get_revision", "get_current", "replace_draft", "approve", "archive"],
)
async def test_invalid_mandate_id_is_rejected_before_persistence(
    invalid_mandate_id: object,
    method_name: str,
) -> None:
    repository = PostgresOperationalMandateRepository(cast(Database, _DatabaseMustNotBeAccessed()))
    mandate_id = cast(UUID, invalid_mandate_id)

    with pytest.raises(InvalidOperationalMandateSpecificationError):
        if method_name == "get":
            await repository.get(mandate_id)
        elif method_name == "get_revision":
            await repository.get_revision(mandate_id, 1)
        elif method_name == "get_current":
            await repository.get_current(mandate_id)
        elif method_name == "replace_draft":
            await repository.replace_draft(
                mandate_id,
                _specification(),
                expected_revision=1,
                expected_record_version=1,
                actor_id=uuid4(),
                now=BASE_TIME,
            )
        elif method_name == "approve":
            await repository.approve(
                mandate_id,
                expected_revision=1,
                expected_checksum="a" * 64,
                expected_record_version=1,
                actor_id=uuid4(),
                now=BASE_TIME,
            )
        else:
            await repository.archive(
                mandate_id,
                expected_record_version=1,
                actor_id=uuid4(),
                now=BASE_TIME,
            )


async def test_missing_reads_return_none(database: Database) -> None:
    repository = PostgresOperationalMandateRepository(database)
    mandate_id = uuid4()

    assert await repository.get(mandate_id) is None
    assert await repository.get_revision(mandate_id, 1) is None
    assert await repository.get_current(mandate_id) is None


async def test_create_round_trip_reconstructs_exact_state(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresOperationalMandateRepository(database)
    specification = _specification(
        instruments=(_instrument("ETH"), _instrument("BTC")),
    )

    aggregate, revision = await _create(
        repository,
        admin_user_id,
        specification=specification,
    )

    assert aggregate.state is OperationalMandateState.DRAFT
    assert aggregate.current_revision == 1
    assert aggregate.record_version == 1
    assert aggregate.created_by == admin_user_id
    assert aggregate.created_at == BASE_TIME
    assert aggregate.approved_revision is None
    assert aggregate.archived_at is None
    assert revision.mandate_id == aggregate.mandate_id
    assert revision.specification == specification
    assert revision.created_by == admin_user_id
    assert revision.created_at == BASE_TIME
    assert await repository.get(aggregate.mandate_id) == aggregate
    assert await repository.get_revision(aggregate.mandate_id, 1) == revision
    assert await repository.get_current(aggregate.mandate_id) == (aggregate, revision)


async def test_create_persists_canonical_hashes_order_and_deduplication(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresOperationalMandateRepository(database)
    specification = _specification(
        instruments=(
            _instrument("ETH"),
            _instrument("BTC"),
            _instrument("ETH"),
        )
    )

    aggregate, revision = await _create(
        repository,
        admin_user_id,
        specification=specification,
    )

    assert tuple(item.pair.base for item in revision.specification.instruments) == (
        "BTC",
        "ETH",
    )
    assert revision.specification_checksum == operational_mandate_specification_checksum(
        specification
    )
    assert aggregate.create_request_fingerprint == (
        operational_mandate_create_request_fingerprint(specification)
    )
    assert await _counts(database, aggregate.mandate_id) == (1, 2)
    assert {field.name for field in fields(OperationalMandateInstrument)} == {
        "exchange",
        "market_type",
        "pair",
    }


async def test_get_current_tracks_exact_replaced_revision(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresOperationalMandateRepository(database)
    created, _ = await _create(repository, admin_user_id)
    changed = _specification(name="Changed", instruments=(_instrument("ETH"),))

    aggregate, revision = await repository.replace_draft(
        created.mandate_id,
        changed,
        expected_revision=1,
        expected_record_version=1,
        actor_id=admin_user_id,
        now=BASE_TIME + timedelta(seconds=1),
    )

    assert aggregate.current_revision == 2
    assert revision.revision == 2
    assert await repository.get_current(created.mandate_id) == (aggregate, revision)


async def test_persisted_checksum_corruption_becomes_persistence_error(
    database: Database,
    database_url: str,
    admin_user_id: UUID,
) -> None:
    repository = PostgresOperationalMandateRepository(database)
    aggregate, _ = await _create(repository, admin_user_id)

    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(
            """
            alter table public.operational_mandate_revisions
            disable trigger operational_mandate_revisions_reject_update_delete
            """
        )
        connection.execute(
            """
            update public.operational_mandate_revisions
            set specification_checksum = repeat('f', 64)
            where mandate_id = %s and revision = 1
            """,
            (aggregate.mandate_id,),
        )

    with pytest.raises(PersistenceError):
        await repository.get_revision(aggregate.mandate_id, 1)
    with pytest.raises(PersistenceError):
        await repository.get_current(aggregate.mandate_id)


async def test_same_actor_key_and_fingerprint_returns_original_without_rewrite(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresOperationalMandateRepository(database)
    first = await _create(repository, admin_user_id)
    replay = await _create(
        repository,
        admin_user_id,
        now=BASE_TIME + timedelta(hours=1),
    )

    assert replay == first
    assert replay[0].created_at == BASE_TIME
    assert replay[0].record_version == 1
    assert await _counts(database, first[0].mandate_id) == (1, 1)


async def test_same_actor_key_with_different_fingerprint_conflicts(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresOperationalMandateRepository(database)
    original = await _create(repository, admin_user_id)

    with pytest.raises(OperationalMandateIdempotencyConflictError):
        await _create(
            repository,
            admin_user_id,
            specification=_specification(name="Different"),
        )

    assert await repository.get_current(original[0].mandate_id) == original
    assert await _total_rows(database) == (1, 1, 1)


async def test_idempotency_scope_and_new_intent_are_distinct(
    database: Database,
    database_url: str,
    admin_user_id: UUID,
) -> None:
    repository = PostgresOperationalMandateRepository(database)
    other_actor = _add_actor(database_url)

    first = await _create(repository, admin_user_id, idempotency_key="same-key")
    cross_actor = await _create(repository, other_actor, idempotency_key="same-key")
    new_intent = await _create(repository, admin_user_id, idempotency_key="new-key")

    assert len({first[0].mandate_id, cross_actor[0].mandate_id, new_intent[0].mandate_id}) == 3


async def test_concurrent_identical_create_converges_without_orphans(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresOperationalMandateRepository(database)

    first, second = await asyncio.gather(
        _create(repository, admin_user_id, idempotency_key="concurrent"),
        _create(repository, admin_user_id, idempotency_key="concurrent"),
    )

    assert first == second
    assert await _total_rows(database) == (1, 1, 1)


async def test_concurrent_divergent_create_has_one_winner_and_safe_conflict(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresOperationalMandateRepository(database)

    results = await asyncio.gather(
        _create(
            repository,
            admin_user_id,
            idempotency_key="divergent",
            specification=_specification(name="First"),
        ),
        _create(
            repository,
            admin_user_id,
            idempotency_key="divergent",
            specification=_specification(name="Second"),
        ),
        return_exceptions=True,
    )

    assert sum(isinstance(result, tuple) for result in results) == 1
    assert (
        sum(isinstance(result, OperationalMandateIdempotencyConflictError) for result in results)
        == 1
    )
    assert await _total_rows(database) == (1, 1, 1)


async def test_changed_draft_appends_revision_and_preserves_history(
    database: Database,
    database_url: str,
    admin_user_id: UUID,
) -> None:
    repository = PostgresOperationalMandateRepository(database)
    original_aggregate, original_revision = await _create(repository, admin_user_id)
    other_actor = _add_actor(database_url)
    changed_at = BASE_TIME + timedelta(seconds=1)
    changed = _specification(name="Revision two", instruments=(_instrument("ETH"),))

    aggregate, revision = await repository.replace_draft(
        original_aggregate.mandate_id,
        changed,
        expected_revision=1,
        expected_record_version=1,
        actor_id=other_actor,
        now=changed_at,
    )

    assert aggregate.state is OperationalMandateState.DRAFT
    assert aggregate.current_revision == 2
    assert aggregate.record_version == 2
    assert revision.revision == 2
    assert revision.created_by == other_actor
    assert revision.created_at == changed_at
    assert await repository.get_revision(aggregate.mandate_id, 1) == original_revision
    assert await _counts(database, aggregate.mandate_id) == (2, 2)


async def test_semantic_noop_preserves_all_audit_and_concurrency_state(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresOperationalMandateRepository(database)
    specification = _specification(
        name="  Mandate  ",
        instruments=(_instrument("ETH"), _instrument("BTC")),
    )
    original = await _create(repository, admin_user_id, specification=specification)
    equivalent = _specification(
        name="Mandate",
        instruments=(_instrument("BTC"), _instrument("ETH"), _instrument("BTC")),
    )

    replay = await repository.replace_draft(
        original[0].mandate_id,
        equivalent,
        expected_revision=1,
        expected_record_version=1,
        actor_id=uuid4(),
        now=BASE_TIME + timedelta(days=1),
    )

    assert replay == original
    assert await _counts(database, original[0].mandate_id) == (1, 2)


async def test_semantic_comparison_is_not_checksum_only(
    database: Database,
    admin_user_id: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = PostgresOperationalMandateRepository(database)
    original, original_revision = await _create(repository, admin_user_id)

    def collision_checksum(_: OperationalMandateSpecification) -> str:
        return original_revision.specification_checksum

    monkeypatch.setattr(
        repository_module,
        "operational_mandate_specification_checksum",
        collision_checksum,
    )
    monkeypatch.setattr(
        mandate_domain,
        "operational_mandate_specification_checksum",
        collision_checksum,
    )

    updated, revision = await repository.replace_draft(
        original.mandate_id,
        _specification(name="Different semantics"),
        expected_revision=1,
        expected_record_version=1,
        actor_id=admin_user_id,
        now=BASE_TIME + timedelta(seconds=1),
    )

    assert updated.current_revision == 2
    assert revision.revision == 2
    assert revision.specification.name == "Different semantics"
    assert revision.specification_checksum == original_revision.specification_checksum


@pytest.mark.parametrize(
    ("expected_revision", "expected_record_version", "error_type"),
    [
        (2, 1, OperationalMandateRevisionConflictError),
        (1, 2, OperationalMandateRecordVersionConflictError),
    ],
)
async def test_stale_tokens_conflict_before_noop(
    database: Database,
    admin_user_id: UUID,
    expected_revision: int,
    expected_record_version: int,
    error_type: type[Exception],
) -> None:
    repository = PostgresOperationalMandateRepository(database)
    aggregate, _ = await _create(repository, admin_user_id)

    with pytest.raises(error_type):
        await repository.replace_draft(
            aggregate.mandate_id,
            _specification(),
            expected_revision=expected_revision,
            expected_record_version=expected_record_version,
            actor_id=admin_user_id,
            now=BASE_TIME,
        )


async def test_replace_missing_is_not_found(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresOperationalMandateRepository(database)

    with pytest.raises(OperationalMandateNotFoundError):
        await repository.replace_draft(
            uuid4(),
            _specification(),
            expected_revision=1,
            expected_record_version=1,
            actor_id=admin_user_id,
            now=BASE_TIME,
        )


@pytest.mark.parametrize("terminal", ["APPROVED", "ARCHIVED"])
async def test_replace_is_forbidden_outside_draft(
    database: Database,
    admin_user_id: UUID,
    terminal: str,
) -> None:
    repository = PostgresOperationalMandateRepository(database)
    aggregate, revision = await _create(repository, admin_user_id)
    if terminal == "APPROVED":
        current = await repository.approve(
            aggregate.mandate_id,
            expected_revision=1,
            expected_checksum=revision.specification_checksum,
            expected_record_version=1,
            actor_id=admin_user_id,
            now=BASE_TIME + timedelta(seconds=1),
        )
    else:
        current = await repository.archive(
            aggregate.mandate_id,
            expected_record_version=1,
            actor_id=admin_user_id,
            now=BASE_TIME + timedelta(seconds=1),
        )

    with pytest.raises(OperationalMandateStateTransitionConflictError):
        await repository.replace_draft(
            aggregate.mandate_id,
            _specification(name="Changed"),
            expected_revision=current.current_revision,
            expected_record_version=current.record_version,
            actor_id=admin_user_id,
            now=BASE_TIME + timedelta(seconds=2),
        )


async def test_approval_seals_exact_revision_without_creating_history(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresOperationalMandateRepository(database)
    aggregate, revision = await _create(repository, admin_user_id)
    approved_at = BASE_TIME + timedelta(seconds=1)

    approved = await repository.approve(
        aggregate.mandate_id,
        expected_revision=1,
        expected_checksum=revision.specification_checksum,
        expected_record_version=1,
        actor_id=admin_user_id,
        now=approved_at,
    )

    assert approved.state is OperationalMandateState.APPROVED
    assert approved.current_revision == 1
    assert approved.record_version == 2
    assert approved.approved_revision == 1
    assert approved.approved_checksum == revision.specification_checksum
    assert approved.approved_by == admin_user_id
    assert approved.approved_at == approved_at
    assert await _counts(database, aggregate.mandate_id) == (1, 1)


@pytest.mark.parametrize(
    ("revision_delta", "version_delta", "checksum", "error_type"),
    [
        (1, 0, None, OperationalMandateRevisionConflictError),
        (0, 1, None, OperationalMandateRecordVersionConflictError),
        (0, 0, "f" * 64, OperationalMandateChecksumMismatchError),
    ],
)
async def test_approval_conflicts_are_specific(
    database: Database,
    admin_user_id: UUID,
    revision_delta: int,
    version_delta: int,
    checksum: str | None,
    error_type: type[Exception],
) -> None:
    repository = PostgresOperationalMandateRepository(database)
    aggregate, revision = await _create(repository, admin_user_id)

    with pytest.raises(error_type):
        await repository.approve(
            aggregate.mandate_id,
            expected_revision=1 + revision_delta,
            expected_checksum=checksum or revision.specification_checksum,
            expected_record_version=1 + version_delta,
            actor_id=admin_user_id,
            now=BASE_TIME + timedelta(seconds=1),
        )


async def test_exact_approval_replay_preserves_original_audit(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresOperationalMandateRepository(database)
    aggregate, revision = await _create(repository, admin_user_id)
    approved = await repository.approve(
        aggregate.mandate_id,
        expected_revision=1,
        expected_checksum=revision.specification_checksum,
        expected_record_version=1,
        actor_id=admin_user_id,
        now=BASE_TIME + timedelta(seconds=1),
    )

    replay = await repository.approve(
        aggregate.mandate_id,
        expected_revision=1,
        expected_checksum=revision.specification_checksum,
        expected_record_version=1,
        actor_id=admin_user_id,
        now=BASE_TIME + timedelta(days=1),
    )

    assert replay == approved
    assert replay.approved_at == BASE_TIME + timedelta(seconds=1)
    assert replay.record_version == 2


@pytest.mark.parametrize("difference", ["revision", "checksum", "actor", "version"])
async def test_non_exact_approval_replay_conflicts(
    database: Database,
    admin_user_id: UUID,
    difference: str,
) -> None:
    repository = PostgresOperationalMandateRepository(database)
    aggregate, revision = await _create(repository, admin_user_id)
    await repository.approve(
        aggregate.mandate_id,
        expected_revision=1,
        expected_checksum=revision.specification_checksum,
        expected_record_version=1,
        actor_id=admin_user_id,
        now=BASE_TIME + timedelta(seconds=1),
    )
    expected_revision = 2 if difference == "revision" else 1
    expected_checksum = "f" * 64 if difference == "checksum" else revision.specification_checksum
    actor_id = uuid4() if difference == "actor" else admin_user_id
    expected_version = 2 if difference == "version" else 1

    with pytest.raises(OperationalMandateStateTransitionConflictError):
        await repository.approve(
            aggregate.mandate_id,
            expected_revision=expected_revision,
            expected_checksum=expected_checksum,
            expected_record_version=expected_version,
            actor_id=actor_id,
            now=BASE_TIME + timedelta(seconds=2),
        )


async def test_draft_archive_and_exact_replay_preserve_audit(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresOperationalMandateRepository(database)
    aggregate, _ = await _create(repository, admin_user_id)
    archived_at = BASE_TIME + timedelta(seconds=1)

    archived = await repository.archive(
        aggregate.mandate_id,
        expected_record_version=1,
        actor_id=admin_user_id,
        now=archived_at,
    )
    replay = await repository.archive(
        aggregate.mandate_id,
        expected_record_version=1,
        actor_id=admin_user_id,
        now=BASE_TIME + timedelta(days=1),
    )

    assert archived.state is OperationalMandateState.ARCHIVED
    assert archived.record_version == 2
    assert archived.current_revision == 1
    assert archived.approved_revision is None
    assert archived.archived_by == admin_user_id
    assert archived.archived_at == archived_at
    assert replay == archived


async def test_approved_archive_preserves_all_approval_metadata(
    database: Database,
    admin_user_id: UUID,
) -> None:
    repository = PostgresOperationalMandateRepository(database)
    aggregate, revision = await _create(repository, admin_user_id)
    approved = await repository.approve(
        aggregate.mandate_id,
        expected_revision=1,
        expected_checksum=revision.specification_checksum,
        expected_record_version=1,
        actor_id=admin_user_id,
        now=BASE_TIME + timedelta(seconds=1),
    )

    archived = await repository.archive(
        aggregate.mandate_id,
        expected_record_version=2,
        actor_id=admin_user_id,
        now=BASE_TIME + timedelta(seconds=2),
    )

    assert archived.state is OperationalMandateState.ARCHIVED
    assert archived.record_version == 3
    assert archived.current_revision == approved.current_revision
    assert archived.approved_revision == approved.approved_revision
    assert archived.approved_checksum == approved.approved_checksum
    assert archived.approved_by == approved.approved_by
    assert archived.approved_at == approved.approved_at


@pytest.mark.parametrize("difference", ["actor", "version"])
async def test_non_exact_archive_replay_conflicts(
    database: Database,
    admin_user_id: UUID,
    difference: str,
) -> None:
    repository = PostgresOperationalMandateRepository(database)
    aggregate, _ = await _create(repository, admin_user_id)
    await repository.archive(
        aggregate.mandate_id,
        expected_record_version=1,
        actor_id=admin_user_id,
        now=BASE_TIME + timedelta(seconds=1),
    )

    with pytest.raises(OperationalMandateStateTransitionConflictError):
        await repository.archive(
            aggregate.mandate_id,
            expected_record_version=2 if difference == "version" else 1,
            actor_id=uuid4() if difference == "actor" else admin_user_id,
            now=BASE_TIME + timedelta(seconds=2),
        )


@pytest.mark.parametrize("value", [True, 0, -1, cast(int, "1")])
async def test_invalid_expected_revision_is_revision_conflict(
    database: Database,
    admin_user_id: UUID,
    value: int,
) -> None:
    repository = PostgresOperationalMandateRepository(database)
    aggregate, _ = await _create(repository, admin_user_id)

    with pytest.raises(OperationalMandateRevisionConflictError):
        await repository.replace_draft(
            aggregate.mandate_id,
            _specification(),
            expected_revision=value,
            expected_record_version=1,
            actor_id=admin_user_id,
            now=BASE_TIME,
        )


@pytest.mark.parametrize("value", [True, 0, -1, cast(int, "1")])
async def test_invalid_expected_record_version_is_record_conflict(
    database: Database,
    admin_user_id: UUID,
    value: int,
) -> None:
    repository = PostgresOperationalMandateRepository(database)
    aggregate, _ = await _create(repository, admin_user_id)

    with pytest.raises(OperationalMandateRecordVersionConflictError):
        await repository.archive(
            aggregate.mandate_id,
            expected_record_version=value,
            actor_id=admin_user_id,
            now=BASE_TIME,
        )


@pytest.mark.parametrize("checksum", ["A" * 64, "a" * 63, "not-a-checksum"])
async def test_malformed_expected_checksum_is_checksum_mismatch(
    database: Database,
    admin_user_id: UUID,
    checksum: str,
) -> None:
    repository = PostgresOperationalMandateRepository(database)
    aggregate, _ = await _create(repository, admin_user_id)

    with pytest.raises(OperationalMandateChecksumMismatchError):
        await repository.approve(
            aggregate.mandate_id,
            expected_revision=1,
            expected_checksum=checksum,
            expected_record_version=1,
            actor_id=admin_user_id,
            now=BASE_TIME,
        )


@pytest.mark.parametrize(
    "invalid_now",
    [
        datetime(2026, 8, 21, 12),
        datetime(2026, 8, 21, 9, tzinfo=timezone(timedelta(hours=-3))),
    ],
)
async def test_non_exact_utc_timestamp_is_rejected_before_persistence(
    database: Database,
    admin_user_id: UUID,
    invalid_now: datetime,
) -> None:
    repository = PostgresOperationalMandateRepository(database)

    with pytest.raises(InvalidOperationalMandateSpecificationError):
        await _create(repository, admin_user_id, now=invalid_now)
    assert await _total_rows(database) == (0, 0, 0)


@pytest.mark.parametrize("key", [" leading", "trailing ", "contains space"])
async def test_idempotency_key_is_not_normalized(
    database: Database,
    admin_user_id: UUID,
    key: str,
) -> None:
    repository = PostgresOperationalMandateRepository(database)

    with pytest.raises(InvalidOperationalMandateSpecificationError):
        await _create(repository, admin_user_id, idempotency_key=key)
    assert await _total_rows(database) == (0, 0, 0)


async def test_persistence_requires_auth_identity_but_not_admin_membership(
    database: Database,
    auth_user_id: UUID,
) -> None:
    repository = PostgresOperationalMandateRepository(database)

    aggregate, _ = await _create(repository, auth_user_id)

    assert aggregate.created_by == auth_user_id
    async with database.transaction() as connection:
        cursor = await connection.execute(
            "select count(*) as total from public.app_admins where user_id = %s",
            (auth_user_id,),
        )
        row = await cursor.fetchone()
    assert row is not None
    assert int(row["total"]) == 0


async def test_unknown_database_failure_is_safely_translated_and_rolls_back(
    database: Database,
) -> None:
    repository = PostgresOperationalMandateRepository(database)

    with pytest.raises(PersistenceError):
        await _create(repository, uuid4())

    assert await _total_rows(database) == (0, 0, 0)
