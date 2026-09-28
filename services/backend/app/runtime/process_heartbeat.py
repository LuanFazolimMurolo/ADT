"""Local process-liveness heartbeat for production supervisors.

This module reports process liveness only. It does not represent trading,
worker, lease, capital, or operational authority.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final


PROCESS_HEARTBEAT_SCHEMA_VERSION: Final = 1

PROCESS_HEARTBEAT_DIRECTORY: Final = "runtime-health"
PROCESS_HEARTBEAT_ROOT: Final = Path("/tmp/adt-runtime-health")

PROCESS_HEARTBEAT_NAMES: Final[frozenset[str]] = frozenset(
    {
        "market-supervisor",
        "paper-supervisor",
    }
)


class InvalidProcessHeartbeatError(ValueError):
    """Raised when heartbeat input or persisted state is invalid."""


class StaleProcessHeartbeatError(RuntimeError):
    """Raised when a process heartbeat is older than its health bound."""


@dataclass(frozen=True, slots=True)
class ProcessHeartbeat:
    schema_version: int
    process: str
    heartbeat_at: datetime


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_process_name(value: str) -> str:
    if value not in PROCESS_HEARTBEAT_NAMES:
        raise InvalidProcessHeartbeatError(
            "unsupported process heartbeat name"
        )
    return value


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise InvalidProcessHeartbeatError(
            "heartbeat timestamp must be timezone-aware"
        )

    normalized = value.astimezone(UTC)

    if normalized.utcoffset() is None:
        raise InvalidProcessHeartbeatError(
            "heartbeat timestamp must resolve to UTC"
        )

    return normalized


def _require_stale_after_seconds(value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise InvalidProcessHeartbeatError(
            "stale-after seconds must be finite and positive"
        )

    return float(value)


def heartbeat_path(
    data_dir: Path,
    process: str,
) -> Path:
    process = _require_process_name(process)

    return (
        data_dir
        / PROCESS_HEARTBEAT_DIRECTORY
        / f"{process}.json"
    )


def write_process_heartbeat(
    data_dir: Path,
    process: str,
    *,
    now: datetime | None = None,
) -> ProcessHeartbeat:
    """Atomically publish one process-liveness heartbeat."""

    process = _require_process_name(process)

    if not data_dir.is_absolute():
        raise InvalidProcessHeartbeatError(
            "data directory must be absolute"
        )

    timestamp = _require_utc(
        _utc_now() if now is None else now
    )

    directory = (
        data_dir
        / PROCESS_HEARTBEAT_DIRECTORY
    )

    directory.mkdir(
        parents=True,
        exist_ok=True,
        mode=0o750,
    )

    destination = heartbeat_path(
        data_dir,
        process,
    )

    temporary = directory / (
        f".{process}.{os.getpid()}.tmp"
    )

    payload = {
        "schema_version": PROCESS_HEARTBEAT_SCHEMA_VERSION,
        "process": process,
        "heartbeat_at": timestamp.isoformat(),
    }

    encoded = (
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")

    try:
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(
            temporary,
            destination,
        )

        directory_fd = os.open(
            directory,
            os.O_RDONLY,
        )

        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass

    return ProcessHeartbeat(
        schema_version=PROCESS_HEARTBEAT_SCHEMA_VERSION,
        process=process,
        heartbeat_at=timestamp,
    )


def read_process_heartbeat(
    data_dir: Path,
    process: str,
) -> ProcessHeartbeat:
    """Read and validate one process-liveness heartbeat."""

    process = _require_process_name(process)

    path = heartbeat_path(
        data_dir,
        process,
    )

    try:
        raw = json.loads(
            path.read_text(
                encoding="utf-8",
            )
        )
    except (
        FileNotFoundError,
        json.JSONDecodeError,
        OSError,
    ) as error:
        raise InvalidProcessHeartbeatError(
            "process heartbeat is unavailable or invalid"
        ) from error

    if not isinstance(raw, dict):
        raise InvalidProcessHeartbeatError(
            "process heartbeat root must be an object"
        )

    if raw.get("schema_version") != (
        PROCESS_HEARTBEAT_SCHEMA_VERSION
    ):
        raise InvalidProcessHeartbeatError(
            "unsupported process heartbeat schema"
        )

    if raw.get("process") != process:
        raise InvalidProcessHeartbeatError(
            "process heartbeat identity mismatch"
        )

    raw_timestamp = raw.get("heartbeat_at")

    if not isinstance(raw_timestamp, str):
        raise InvalidProcessHeartbeatError(
            "process heartbeat timestamp missing"
        )

    try:
        parsed = datetime.fromisoformat(
            raw_timestamp,
        )
    except ValueError as error:
        raise InvalidProcessHeartbeatError(
            "process heartbeat timestamp invalid"
        ) from error

    heartbeat_at = _require_utc(
        parsed,
    )

    return ProcessHeartbeat(
        schema_version=PROCESS_HEARTBEAT_SCHEMA_VERSION,
        process=process,
        heartbeat_at=heartbeat_at,
    )


def require_fresh_process_heartbeat(
    data_dir: Path,
    process: str,
    *,
    stale_after_seconds: float,
    now: datetime | None = None,
) -> ProcessHeartbeat:
    """Return heartbeat only when it is valid and sufficiently fresh."""

    stale_after_seconds = (
        _require_stale_after_seconds(
            stale_after_seconds
        )
    )

    observed_at = _require_utc(
        _utc_now() if now is None else now
    )

    heartbeat = read_process_heartbeat(
        data_dir,
        process,
    )

    age = (
        observed_at
        - heartbeat.heartbeat_at
    ).total_seconds()

    if age < 0:
        raise InvalidProcessHeartbeatError(
            "process heartbeat is from the future"
        )

    if age > stale_after_seconds:
        raise StaleProcessHeartbeatError(
            "process heartbeat is stale"
        )

    return heartbeat



@asynccontextmanager
async def publish_process_heartbeat(
    data_dir: Path,
    process: str,
    *,
    interval_seconds: float = 30.0,
):
    """Publish liveness while the surrounding process loop remains alive."""

    interval_seconds = (
        _require_stale_after_seconds(
            interval_seconds
        )
    )

    write_process_heartbeat(
        data_dir,
        process,
    )

    async def publish_loop() -> None:
        while True:
            await asyncio.sleep(
                interval_seconds
            )

            write_process_heartbeat(
                data_dir,
                process,
            )

    task = asyncio.create_task(
        publish_loop(),
        name=(
            "adt-process-heartbeat-"
            f"{process}"
        ),
    )

    try:
        yield
    finally:
        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate one ADT supervisor "
            "process heartbeat."
        )
    )

    parser.add_argument(
        "--data-dir",
        required=True,
    )

    parser.add_argument(
        "--process",
        choices=sorted(
            PROCESS_HEARTBEAT_NAMES
        ),
        required=True,
    )

    parser.add_argument(
        "--stale-after-seconds",
        type=float,
        required=True,
    )

    return parser


def main() -> int:
    args = _build_parser().parse_args()

    try:
        heartbeat = (
            require_fresh_process_heartbeat(
                Path(args.data_dir),
                args.process,
                stale_after_seconds=(
                    args.stale_after_seconds
                ),
            )
        )
    except (
        InvalidProcessHeartbeatError,
        StaleProcessHeartbeatError,
    ) as error:
        print(
            f"UNHEALTHY: {error}",
        )
        return 1

    print(
        "HEALTHY:",
        heartbeat.process,
        heartbeat.heartbeat_at.isoformat(),
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
