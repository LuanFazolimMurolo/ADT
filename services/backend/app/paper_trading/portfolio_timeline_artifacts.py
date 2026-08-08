"""Atomic content-addressed storage for deterministic paper portfolio timelines."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq

from app.backtesting.serialization import (
    canonical_json_bytes,
    decimal_text,
    file_checksum,
)
from app.market_data.domain import DataRange
from app.market_data.filesystem import ensure_safe_path, fsync_directory, market_root
from app.market_data.locks import DatasetLockManager
from app.paper_trading.errors import PaperSessionCorruptError
from app.paper_trading.portfolio_timeline import (
    PaperPortfolioObservation,
    PaperPortfolioTimeline,
    validate_paper_portfolio_timeline,
)

_MANIFEST_NAME = "manifest.json"
_OBSERVATIONS_NAME = "observations.parquet"
_MANIFEST_VERSION = 1
_MAX_MANIFEST_BYTES = 64 * 1024

_MANIFEST_KEYS = frozenset(
    {
        "manifest_version",
        "timeline_schema_version",
        "session_id",
        "config_checksum",
        "state_id",
        "state_checksum",
        "engine_version",
        "strategy_lifecycle_version",
        "base_asset",
        "quote_asset",
        "timeframe",
        "dataset_version",
        "source_checksum",
        "data_range",
        "evaluation_range",
        "initial_capital",
        "candles_processed",
        "timeline_id",
        "content_checksum",
        "observations_file",
        "observations_file_checksum",
        "observations_size_bytes",
    }
)
_RANGE_KEYS = frozenset({"start", "end"})


class PaperPortfolioTimelineArtifactStore:
    """Publish immutable timeline directories and verify them before every read."""

    def __init__(
        self,
        data_dir: Path,
        *,
        directory: Path = Path("paper-trading"),
        lock_timeout_seconds: float = 10,
        lock_stale_after_seconds: float = 3_600,
    ) -> None:
        if directory.is_absolute() or not directory.parts or ".." in directory.parts:
            raise PaperSessionCorruptError()
        market = market_root(data_dir)
        self._market = market
        self._paper_root = ensure_safe_path(market, market / directory)
        self._locks = DatasetLockManager(
            data_dir,
            timeout_seconds=lock_timeout_seconds,
            stale_after_seconds=lock_stale_after_seconds,
        )

    def publish(self, timeline: PaperPortfolioTimeline) -> PaperPortfolioTimeline:
        validate_paper_portfolio_timeline(timeline)
        root = self._timeline_root(timeline.session_id)
        root.mkdir(parents=True, exist_ok=True)
        fsync_directory(root.parent)
        target = ensure_safe_path(self._market, root / timeline.timeline_id)

        with self._locks.acquire(f"paper-timeline:{timeline.session_id}:{timeline.timeline_id}"):
            if target.exists():
                persisted = self.load(timeline.session_id, timeline.timeline_id)
                if persisted != timeline:
                    raise PaperSessionCorruptError()
                return persisted

            staging = ensure_safe_path(
                self._market,
                root / f".{timeline.timeline_id}.tmp-{os.getpid()}-{uuid4().hex}",
            )
            try:
                staging.mkdir(parents=False, exist_ok=False)
                observations_path = ensure_safe_path(self._market, staging / _OBSERVATIONS_NAME)
                _write_observations(observations_path, timeline.observations)
                _fsync_file(observations_path)

                manifest = _manifest_payload(
                    timeline,
                    observations_checksum=file_checksum(observations_path),
                    observations_size=observations_path.stat().st_size,
                )
                manifest_path = ensure_safe_path(self._market, staging / _MANIFEST_NAME)
                manifest_path.write_bytes(_manifest_document(manifest))
                _fsync_file(manifest_path)
                fsync_directory(staging)

                os.replace(staging, target)
                fsync_directory(root)
            except Exception:
                _remove_tree(staging)
                raise

        persisted = self.load(timeline.session_id, timeline.timeline_id)
        if persisted != timeline:
            raise PaperSessionCorruptError()
        return persisted

    def load(self, session_id: str, timeline_id: str) -> PaperPortfolioTimeline:
        _sha256(session_id)
        _sha256(timeline_id)
        target = ensure_safe_path(
            self._market,
            self._timeline_root(session_id) / timeline_id,
        )
        if not target.is_dir():
            raise PaperSessionCorruptError()

        manifest_path = ensure_safe_path(self._market, target / _MANIFEST_NAME)
        observations_path = ensure_safe_path(self._market, target / _OBSERVATIONS_NAME)
        manifest = _read_manifest(manifest_path)

        if (
            manifest["session_id"] != session_id
            or manifest["timeline_id"] != timeline_id
            or manifest["observations_file"] != _OBSERVATIONS_NAME
        ):
            raise PaperSessionCorruptError()

        expected_file_checksum = _string(manifest["observations_file_checksum"])
        expected_size = _int(manifest["observations_size_bytes"])
        try:
            actual_size = observations_path.stat().st_size
        except OSError:
            raise PaperSessionCorruptError() from None
        if (
            actual_size != expected_size
            or file_checksum(observations_path) != expected_file_checksum
        ):
            raise PaperSessionCorruptError()

        candles_processed = _int(manifest["candles_processed"])
        try:
            parquet = pq.ParquetFile(observations_path)
            if parquet.metadata.num_rows != candles_processed:
                raise ValueError
            rows = parquet.read().to_pylist()
        except Exception:
            raise PaperSessionCorruptError() from None

        try:
            timeline = _timeline_from_manifest_and_rows(manifest, rows)
            validate_paper_portfolio_timeline(timeline)
            if (
                timeline.timeline_id != timeline_id
                or timeline.content_checksum != manifest["content_checksum"]
            ):
                raise ValueError
            return timeline
        except PaperSessionCorruptError:
            raise
        except Exception:
            raise PaperSessionCorruptError() from None

    def _timeline_root(self, session_id: str) -> Path:
        _sha256(session_id)
        return ensure_safe_path(
            self._market,
            self._paper_root / session_id / "portfolio-timelines",
        )


def _write_observations(
    path: Path,
    observations: tuple[PaperPortfolioObservation, ...],
) -> None:
    rows = [
        {
            "candle_index": item.candle_index,
            "candle_open_time": item.candle_open_time.isoformat(),
            "candle_close_time": item.candle_close_time.isoformat(),
            "mark_price": decimal_text(item.mark_price),
            "quote_cash": decimal_text(item.quote_cash),
            "base_quantity": decimal_text(item.base_quantity),
            "average_entry_price": decimal_text(item.average_entry_price),
            "cost_basis": decimal_text(item.cost_basis),
            "realized_pnl": decimal_text(item.realized_pnl),
            "unrealized_pnl": decimal_text(item.unrealized_pnl),
            "total_fees": decimal_text(item.total_fees),
            "total_slippage_cost": decimal_text(item.total_slippage_cost),
            "equity": decimal_text(item.equity),
            "peak_equity": decimal_text(item.peak_equity),
            "drawdown": decimal_text(item.drawdown),
            "drawdown_pct": decimal_text(item.drawdown_pct),
            "risk_halt": item.risk_halt,
        }
        for item in observations
    ]
    schema = pa.schema(
        [
            pa.field("candle_index", pa.int64(), nullable=False),
            pa.field("candle_open_time", pa.string(), nullable=False),
            pa.field("candle_close_time", pa.string(), nullable=False),
            pa.field("mark_price", pa.string(), nullable=False),
            pa.field("quote_cash", pa.string(), nullable=False),
            pa.field("base_quantity", pa.string(), nullable=False),
            pa.field("average_entry_price", pa.string(), nullable=False),
            pa.field("cost_basis", pa.string(), nullable=False),
            pa.field("realized_pnl", pa.string(), nullable=False),
            pa.field("unrealized_pnl", pa.string(), nullable=False),
            pa.field("total_fees", pa.string(), nullable=False),
            pa.field("total_slippage_cost", pa.string(), nullable=False),
            pa.field("equity", pa.string(), nullable=False),
            pa.field("peak_equity", pa.string(), nullable=False),
            pa.field("drawdown", pa.string(), nullable=False),
            pa.field("drawdown_pct", pa.string(), nullable=False),
            pa.field("risk_halt", pa.bool_(), nullable=False),
        ]
    )
    table = pa.Table.from_pylist(rows, schema=schema)
    pq.write_table(table, path, compression="snappy", version="2.6")


def _manifest_payload(
    timeline: PaperPortfolioTimeline,
    *,
    observations_checksum: str,
    observations_size: int,
) -> dict[str, object]:
    return {
        "manifest_version": _MANIFEST_VERSION,
        "timeline_schema_version": timeline.schema_version,
        "session_id": timeline.session_id,
        "config_checksum": timeline.config_checksum,
        "state_id": timeline.state_id,
        "state_checksum": timeline.state_checksum,
        "engine_version": timeline.engine_version,
        "strategy_lifecycle_version": timeline.strategy_lifecycle_version,
        "base_asset": timeline.base_asset,
        "quote_asset": timeline.quote_asset,
        "timeframe": timeline.timeframe,
        "dataset_version": timeline.dataset_version,
        "source_checksum": timeline.source_checksum,
        "data_range": {
            "start": timeline.data_range.start.isoformat(),
            "end": timeline.data_range.end.isoformat(),
        },
        "evaluation_range": {
            "start": timeline.evaluation_range.start.isoformat(),
            "end": timeline.evaluation_range.end.isoformat(),
        },
        "initial_capital": decimal_text(timeline.initial_capital),
        "candles_processed": timeline.candles_processed,
        "timeline_id": timeline.timeline_id,
        "content_checksum": timeline.content_checksum,
        "observations_file": _OBSERVATIONS_NAME,
        "observations_file_checksum": observations_checksum,
        "observations_size_bytes": observations_size,
    }


def _manifest_document(payload: dict[str, object]) -> bytes:
    checksum = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return canonical_json_bytes({"manifest": payload, "checksum": checksum})


def _read_manifest(path: Path) -> dict[str, object]:
    try:
        raw = path.read_bytes()
        if len(raw) > _MAX_MANIFEST_BYTES:
            raise ValueError

        def reject_duplicates(
            pairs: list[tuple[str, object]],
        ) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError
                result[key] = value
            return result

        document = json.loads(raw, object_pairs_hook=reject_duplicates)
        if not isinstance(document, dict) or set(document) != {"manifest", "checksum"}:
            raise ValueError
        payload = document["manifest"]
        checksum = document["checksum"]
        if (
            not isinstance(payload, dict)
            or frozenset(payload) != _MANIFEST_KEYS
            or not isinstance(checksum, str)
            or hashlib.sha256(canonical_json_bytes(payload)).hexdigest() != checksum
        ):
            raise ValueError
        if _int(payload["manifest_version"]) != _MANIFEST_VERSION:
            raise ValueError
        if _string(payload["observations_file"]) != _OBSERVATIONS_NAME:
            raise ValueError
        _sha256(_string(payload["session_id"]))
        _sha256(_string(payload["config_checksum"]))
        _sha256(_string(payload["state_id"]))
        _sha256(_string(payload["state_checksum"]))
        _sha256(_string(payload["dataset_version"]))
        _sha256(_string(payload["source_checksum"]))
        _sha256(_string(payload["timeline_id"]))
        _sha256(_string(payload["content_checksum"]))
        _sha256(_string(payload["observations_file_checksum"]))
        if _int(payload["observations_size_bytes"]) < 1:
            raise ValueError
        return payload
    except PaperSessionCorruptError:
        raise
    except Exception:
        raise PaperSessionCorruptError() from None


def _timeline_from_manifest_and_rows(
    manifest: dict[str, object],
    rows: list[dict[str, object]],
) -> PaperPortfolioTimeline:
    session_id = _string(manifest["session_id"])
    config_checksum = _string(manifest["config_checksum"])
    state_id = _string(manifest["state_id"])
    dataset_version = _string(manifest["dataset_version"])
    source_checksum = _string(manifest["source_checksum"])

    observations = tuple(
        PaperPortfolioObservation(
            session_id=session_id,
            config_checksum=config_checksum,
            state_id=state_id,
            dataset_version=dataset_version,
            source_checksum=source_checksum,
            candle_index=_int(row["candle_index"]),
            candle_open_time=_datetime(row["candle_open_time"]),
            candle_close_time=_datetime(row["candle_close_time"]),
            mark_price=_decimal(row["mark_price"]),
            quote_cash=_decimal(row["quote_cash"]),
            base_quantity=_decimal(row["base_quantity"]),
            average_entry_price=_decimal(row["average_entry_price"]),
            cost_basis=_decimal(row["cost_basis"]),
            realized_pnl=_decimal(row["realized_pnl"]),
            unrealized_pnl=_decimal(row["unrealized_pnl"]),
            total_fees=_decimal(row["total_fees"]),
            total_slippage_cost=_decimal(row["total_slippage_cost"]),
            equity=_decimal(row["equity"]),
            peak_equity=_decimal(row["peak_equity"]),
            drawdown=_decimal(row["drawdown"]),
            drawdown_pct=_decimal(row["drawdown_pct"]),
            risk_halt=_bool(row["risk_halt"]),
        )
        for row in rows
    )

    return PaperPortfolioTimeline(
        session_id=session_id,
        config_checksum=config_checksum,
        state_id=state_id,
        state_checksum=_string(manifest["state_checksum"]),
        engine_version=_string(manifest["engine_version"]),
        strategy_lifecycle_version=_int(manifest["strategy_lifecycle_version"]),
        base_asset=_string(manifest["base_asset"]),
        quote_asset=_string(manifest["quote_asset"]),
        timeframe=_string(manifest["timeframe"]),
        dataset_version=dataset_version,
        source_checksum=source_checksum,
        data_range=_range(manifest["data_range"]),
        evaluation_range=_range(manifest["evaluation_range"]),
        initial_capital=_decimal(manifest["initial_capital"]),
        candles_processed=_int(manifest["candles_processed"]),
        observations=observations,
        timeline_id=_string(manifest["timeline_id"]),
        content_checksum=_string(manifest["content_checksum"]),
        schema_version=_int(manifest["timeline_schema_version"]),
    )


def _range(value: object) -> DataRange:
    if not isinstance(value, dict) or frozenset(value) != _RANGE_KEYS:
        raise ValueError
    return DataRange(
        _datetime(value["start"]),
        _datetime(value["end"]),
    )


def _datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError
    return datetime.fromisoformat(value)


def _decimal(value: object) -> Decimal:
    if not isinstance(value, str):
        raise ValueError
    parsed = Decimal(value)
    if not parsed.is_finite() or decimal_text(parsed) != value:
        raise ValueError
    return parsed


def _int(value: object) -> int:
    if type(value) is not int:
        raise ValueError
    return value


def _bool(value: object) -> bool:
    if type(value) is not bool:
        raise ValueError
    return value


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError
    return value


def _sha256(value: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError
    return value


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return
    try:
        shutil.rmtree(path)
    except OSError:
        raise PaperSessionCorruptError() from None
