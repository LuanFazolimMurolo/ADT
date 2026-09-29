"""Tests for supervisor process-liveness heartbeat."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.runtime.process_heartbeat import (
    InvalidProcessHeartbeatError,
    StaleProcessHeartbeatError,
    heartbeat_path,
    publish_process_heartbeat,
    read_process_heartbeat,
    require_fresh_process_heartbeat,
    write_process_heartbeat,
)


NOW = datetime(
    2026,
    9,
    27,
    23,
    50,
    tzinfo=UTC,
)


def test_write_and_read_round_trip(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path.resolve()

    written = write_process_heartbeat(
        data_dir,
        "market-supervisor",
        now=NOW,
    )

    loaded = read_process_heartbeat(
        data_dir,
        "market-supervisor",
    )

    assert loaded == written

    payload = json.loads(
        heartbeat_path(
            data_dir,
            "market-supervisor",
        ).read_text(
            encoding="utf-8",
        )
    )

    assert payload == {
        "heartbeat_at": NOW.isoformat(),
        "process": "market-supervisor",
        "schema_version": 1,
    }


def test_write_replaces_existing_heartbeat(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path.resolve()

    write_process_heartbeat(
        data_dir,
        "paper-supervisor",
        now=NOW,
    )

    later = NOW + timedelta(seconds=5)

    write_process_heartbeat(
        data_dir,
        "paper-supervisor",
        now=later,
    )

    loaded = read_process_heartbeat(
        data_dir,
        "paper-supervisor",
    )

    assert loaded.heartbeat_at == later


def test_fresh_heartbeat_passes(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path.resolve()

    write_process_heartbeat(
        data_dir,
        "market-supervisor",
        now=NOW,
    )

    loaded = require_fresh_process_heartbeat(
        data_dir,
        "market-supervisor",
        stale_after_seconds=30,
        now=NOW + timedelta(seconds=29),
    )

    assert loaded.heartbeat_at == NOW


def test_stale_heartbeat_fails_closed(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path.resolve()

    write_process_heartbeat(
        data_dir,
        "market-supervisor",
        now=NOW,
    )

    with pytest.raises(
        StaleProcessHeartbeatError
    ):
        require_fresh_process_heartbeat(
            data_dir,
            "market-supervisor",
            stale_after_seconds=30,
            now=NOW + timedelta(seconds=31),
        )


def test_missing_heartbeat_fails_closed(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        InvalidProcessHeartbeatError
    ):
        read_process_heartbeat(
            tmp_path.resolve(),
            "paper-supervisor",
        )


def test_wrong_process_identity_fails_closed(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path.resolve()

    write_process_heartbeat(
        data_dir,
        "market-supervisor",
        now=NOW,
    )

    source = heartbeat_path(
        data_dir,
        "market-supervisor",
    )

    target = heartbeat_path(
        data_dir,
        "paper-supervisor",
    )

    target.write_bytes(
        source.read_bytes()
    )

    with pytest.raises(
        InvalidProcessHeartbeatError
    ):
        read_process_heartbeat(
            data_dir,
            "paper-supervisor",
        )


def test_future_heartbeat_fails_closed(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path.resolve()

    write_process_heartbeat(
        data_dir,
        "market-supervisor",
        now=NOW + timedelta(seconds=1),
    )

    with pytest.raises(
        InvalidProcessHeartbeatError
    ):
        require_fresh_process_heartbeat(
            data_dir,
            "market-supervisor",
            stale_after_seconds=30,
            now=NOW,
        )



@pytest.mark.asyncio
async def test_async_publisher_writes_immediately(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path.resolve()

    path = heartbeat_path(
        data_dir,
        "market-supervisor",
    )

    assert not path.exists()

    async with publish_process_heartbeat(
        data_dir,
        "market-supervisor",
        interval_seconds=60,
    ):
        assert path.exists()

        loaded = read_process_heartbeat(
            data_dir,
            "market-supervisor",
        )

        assert loaded.process == (
            "market-supervisor"
        )


@pytest.mark.asyncio
async def test_async_publisher_advances_heartbeat(
    tmp_path: Path,
) -> None:
    import asyncio

    data_dir = tmp_path.resolve()

    async with publish_process_heartbeat(
        data_dir,
        "paper-supervisor",
        interval_seconds=0.01,
    ):
        first = read_process_heartbeat(
            data_dir,
            "paper-supervisor",
        )

        await asyncio.sleep(0.03)

        second = read_process_heartbeat(
            data_dir,
            "paper-supervisor",
        )

        assert (
            second.heartbeat_at
            > first.heartbeat_at
        )


@pytest.mark.asyncio
async def test_async_publisher_stops_after_context(
    tmp_path: Path,
) -> None:
    import asyncio

    data_dir = tmp_path.resolve()

    async with publish_process_heartbeat(
        data_dir,
        "market-supervisor",
        interval_seconds=0.01,
    ):
        await asyncio.sleep(0.02)

    stopped = read_process_heartbeat(
        data_dir,
        "market-supervisor",
    ).heartbeat_at

    await asyncio.sleep(0.03)

    after = read_process_heartbeat(
        data_dir,
        "market-supervisor",
    ).heartbeat_at

    assert after == stopped


@pytest.mark.parametrize(
    "process",
    [
        "",
        "api",
        "market",
        "../market-supervisor",
        "paper-supervisor.json",
    ],
)
def test_process_name_is_closed_set(
    tmp_path: Path,
    process: str,
) -> None:
    with pytest.raises(
        InvalidProcessHeartbeatError
    ):
        heartbeat_path(
            tmp_path.resolve(),
            process,
        )


@pytest.mark.parametrize(
    "value",
    [
        0,
        -1,
        float("inf"),
        float("nan"),
        True,
    ],
)
def test_stale_bound_must_be_positive_finite(
    tmp_path: Path,
    value: float,
) -> None:
    data_dir = tmp_path.resolve()

    write_process_heartbeat(
        data_dir,
        "market-supervisor",
        now=NOW,
    )

    with pytest.raises(
        InvalidProcessHeartbeatError
    ):
        require_fresh_process_heartbeat(
            data_dir,
            "market-supervisor",
            stale_after_seconds=value,
            now=NOW,
        )
