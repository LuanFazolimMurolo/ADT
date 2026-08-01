"""CLI tests for bounded visualization and explicit batch comparison."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest

import app.backtesting.commands as commands_module
from app.backtesting.commands import run_backtest_command
from app.cli import EXIT_OK, build_parser
from app.core.config import MarketDataSettings


def _settings(tmp_path: Path) -> MarketDataSettings:
    return MarketDataSettings(data_dir=tmp_path, market_http_retries=0)


def test_parser_accepts_bounded_visualization_and_batch_file(tmp_path: Path) -> None:
    visualize = build_parser().parse_args(
        [
            "backtest",
            "visualize",
            "--run-id",
            "a" * 64,
            "--max-points",
            "250",
        ]
    )
    batch = build_parser().parse_args(
        [
            "backtest",
            "compare-batch",
            "--request-file",
            str(tmp_path / "batch.json"),
        ]
    )

    assert visualize.max_points == 250
    assert batch.request_file == tmp_path / "batch.json"

    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "backtest",
                "visualize",
                "--run-id",
                "a" * 64,
                "--max-points",
                "2001",
            ]
        )


def test_visualize_command_emits_bounded_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _Reader:
        def __init__(self, data_dir: Path, **kwargs: object) -> None:
            captured["data_dir"] = data_dir
            captured["kwargs"] = kwargs

        def visualization(self, run_id: str, *, max_points: int) -> dict[str, object]:
            captured["run_id"] = run_id
            captured["max_points"] = max_points
            return {
                "contract_version": 1,
                "run_id": run_id,
                "source_point_count": 1000,
                "point_count": max_points,
                "points": [],
            }

    monkeypatch.setattr(commands_module, "BacktestRunReader", _Reader)
    output = StringIO()
    code = run_backtest_command(
        build_parser().parse_args(
            [
                "backtest",
                "visualize",
                "--run-id",
                "a" * 64,
                "--max-points",
                "100",
            ]
        ),
        settings=_settings(tmp_path),
        stdout=output,
    )

    assert code == EXIT_OK
    assert json.loads(output.getvalue())["point_count"] == 100
    assert captured["run_id"] == "a" * 64
    assert captured["max_points"] == 100


def test_compare_batch_loads_explicit_file_and_emits_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_path = tmp_path / "batch.json"
    request_path.write_text(
        json.dumps(
            {
                "contract_version": 1,
                "groups": [
                    {
                        "name": "baseline",
                        "run_ids": ["a" * 64, "b" * 64],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    class _Reader:
        def __init__(self, data_dir: Path, **kwargs: object) -> None:
            del data_dir, kwargs

        def compare_batch(self, request: object) -> dict[str, object]:
            captured["request"] = request
            return {
                "contract_version": 1,
                "batch_id": "c" * 64,
                "group_count": 1,
                "unique_run_count": 2,
                "groups": [],
            }

    monkeypatch.setattr(commands_module, "BacktestRunReader", _Reader)
    output = StringIO()
    code = run_backtest_command(
        build_parser().parse_args(
            [
                "backtest",
                "compare-batch",
                "--request-file",
                str(request_path),
            ]
        ),
        settings=_settings(tmp_path),
        stdout=output,
    )

    assert code == EXIT_OK
    assert json.loads(output.getvalue())["unique_run_count"] == 2
    assert getattr(captured["request"], "unique_run_ids") == ("a" * 64, "b" * 64)
