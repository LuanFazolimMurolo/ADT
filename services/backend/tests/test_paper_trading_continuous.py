"""Remote-free tests for continuous paper execution and read-only API."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from app.api.dependencies.resources import (
    get_paper_runner_state_store,
    get_paper_trading_read_service,
)
from app.api.routes import paper_trading as paper_trading_routes
from app.cli import build_parser
from app.main import app
from app.market_data.locks import DatasetLockManager
from app.paper_trading import continuous as paper_continuous
from app.paper_trading.continuous import (
    PaperRunnerCycleStatus,
    PaperRunnerPolicy,
    PaperRunnerSessionResult,
    PaperRunnerSessionStatus,
    PaperRunnerState,
    PaperRunnerStateStore,
    PaperTradingContinuousRunner,
    PaperTradingContinuousService,
    paper_runner_state_payload,
)
from app.paper_trading.domain import PaperRunAction, PaperRunResult, paper_session_id
from app.paper_trading.errors import (
    PaperRunnerCorruptError,
    PaperRunnerStateNotFoundError,
    PaperSessionNotFoundError,
    PaperTradingError,
)
from app.paper_trading.query import PaperTradingReadService
from app.paper_trading.repository import PaperTradingRepository
from tests.test_paper_trading import FakeSource, _candle, _config, _service


class StubPaperTradingService:
    def __init__(self, outcomes: dict[str, PaperRunResult | Exception]) -> None:
        self.outcomes = outcomes
        self.calls: list[str] = []

    def run_once(self, session_id: str) -> PaperRunResult:
        self.calls.append(session_id)
        outcome = self.outcomes[session_id]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _state(tmp_path: Path, *, candles: int = 2):
    source = FakeSource(tuple(_candle(index) for index in range(candles)))
    service = _service(tmp_path, source)
    config = _config()
    session_id = paper_session_id(config)
    service.create(config)
    state = service.run_once(session_id).state
    return service, config, state


def _runner_state(session_id: str, state_id: str) -> PaperRunnerState:
    started = datetime(2026, 8, 3, tzinfo=UTC)
    result = PaperRunnerSessionResult(
        session_id=session_id,
        status=PaperRunnerSessionStatus.NOOP,
        started_at=started,
        finished_at=started,
        state_id=state_id,
        candles_processed=2,
        last_candle_open_time=started,
    )
    return PaperRunnerState(
        cycle_index=1,
        status=PaperRunnerCycleStatus.COMPLETED,
        policy=PaperRunnerPolicy(30, 10),
        started_at=started,
        finished_at=started,
        next_cycle_at=started + timedelta(seconds=30),
        results=(result,),
    )


@pytest.mark.parametrize("value", [True, 0, -1, 3601, 1.0, "30"])
def test_runner_policy_rejects_invalid_interval(value: object) -> None:
    with pytest.raises(PaperTradingError):
        PaperRunnerPolicy(value, 10)  # type: ignore[arg-type]


def test_runner_cycle_is_sorted_failure_isolated_and_deterministic(tmp_path: Path) -> None:
    _, config, state = _state(tmp_path)
    first = paper_session_id(config)
    second = "f" * 64
    stub = StubPaperTradingService(
        {
            first: PaperRunResult(PaperRunAction.UPDATED, state),
            second: PaperSessionNotFoundError(),
        }
    )
    service = PaperTradingContinuousService(
        stub,
        policy=PaperRunnerPolicy(30, 10),
        clock=lambda: datetime(2026, 8, 3, tzinfo=UTC),
    )

    cycle = service.run_cycle(tuple(sorted((second, first))), cycle_index=1)

    assert cycle.status is PaperRunnerCycleStatus.PARTIALLY_FAILED
    assert tuple(item.session_id for item in cycle.results) == tuple(sorted((first, second)))
    assert cycle.results[0].status is PaperRunnerSessionStatus.UPDATED
    assert cycle.results[1].status is PaperRunnerSessionStatus.FAILED
    assert cycle.results[1].error_code == "paper_session_not_found"
    assert stub.calls == list(sorted((first, second)))
    assert len(cycle.cycle_id) == len(cycle.checksum) == 64


def test_runner_rejects_state_from_another_session(tmp_path: Path) -> None:
    _, config, state = _state(tmp_path)
    actual_session_id = paper_session_id(config)
    requested_session_id = "f" * 64
    assert requested_session_id != actual_session_id
    stub = StubPaperTradingService(
        {requested_session_id: PaperRunResult(PaperRunAction.NOOP, state)}
    )
    service = PaperTradingContinuousService(
        stub,
        policy=PaperRunnerPolicy(30, 10),
        clock=lambda: datetime(2026, 8, 3, tzinfo=UTC),
    )

    cycle = service.run_cycle((requested_session_id,), cycle_index=1)

    assert cycle.status is PaperRunnerCycleStatus.FAILED
    result = cycle.results[0]
    assert result.session_id == requested_session_id
    assert result.status is PaperRunnerSessionStatus.FAILED
    assert result.error_code == "paper_trading_error"
    assert result.state_id is None
    assert result.candles_processed is None
    assert result.last_candle_open_time is None


def test_runner_rejects_mutated_run_result_as_stable_failure(tmp_path: Path) -> None:
    _, config, state = _state(tmp_path)
    session_id = paper_session_id(config)
    result = PaperRunResult(PaperRunAction.NOOP, state)
    object.__setattr__(result, "action", "NOOP")
    stub = StubPaperTradingService({session_id: result})
    service = PaperTradingContinuousService(
        stub,
        policy=PaperRunnerPolicy(30, 10),
        clock=lambda: datetime(2026, 8, 3, tzinfo=UTC),
    )

    cycle = service.run_cycle((session_id,), cycle_index=1)

    assert cycle.status is PaperRunnerCycleStatus.FAILED
    assert cycle.results[0].status is PaperRunnerSessionStatus.FAILED
    assert cycle.results[0].error_code == "paper_trading_error"


def test_runner_rejects_duplicate_or_unsorted_sessions() -> None:
    stub = StubPaperTradingService({})
    service = PaperTradingContinuousService(stub, policy=PaperRunnerPolicy(30, 10))
    session_id = "a" * 64
    with pytest.raises(PaperTradingError):
        service.validate_session_ids((session_id, session_id))
    with pytest.raises(PaperTradingError):
        service.validate_session_ids(("b" * 64, "a" * 64))


def test_state_store_round_trip_and_duplicate_json(tmp_path: Path) -> None:
    state = _runner_state("a" * 64, "b" * 64)
    store = PaperRunnerStateStore(tmp_path)
    assert store.write(state) == state
    assert store.read() == state
    assert store.require() == state

    store.path.write_text('{"schema_version":1,"schema_version":1}')
    with pytest.raises(PaperRunnerCorruptError):
        store.read()


def test_runner_state_bounded_reader_enforces_limit(tmp_path: Path) -> None:
    maximum = paper_continuous._MAX_STATE_BYTES  # noqa: SLF001
    read_bounded = paper_continuous._read_bounded  # noqa: SLF001
    target = tmp_path / "state.json"
    target.write_bytes(b"x" * maximum)
    assert len(read_bounded(target, maximum)) == maximum

    target.write_bytes(b"x" * (maximum + 1))
    with pytest.raises(PaperRunnerCorruptError):
        read_bounded(target, maximum)


def test_runner_state_store_does_not_use_path_read_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _runner_state("a" * 64, "b" * 64)
    store = PaperRunnerStateStore(tmp_path)
    assert store.write(state) == state

    def forbidden_read_bytes(_path: Path) -> bytes:
        raise AssertionError("Path.read_bytes() must not be used")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read_bytes)
    assert store.read() == state


def test_runner_state_bounded_reader_requests_maximum_plus_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "state.json"
    target.write_bytes(b"{}")
    original_open = Path.open
    requested: list[int] = []

    class RecordingStream:
        def __init__(self, stream: object) -> None:
            self._stream = stream

        def __enter__(self) -> RecordingStream:
            return self

        def __exit__(
            self,
            exc_type: object,
            exc: object,
            traceback: object,
        ) -> None:
            self._stream.__exit__(exc_type, exc, traceback)  # type: ignore[attr-defined]

        def read(self, size: int = -1) -> bytes:
            requested.append(size)
            return self._stream.read(size)  # type: ignore[attr-defined,no-any-return]

    def recording_open(
        path: Path,
        mode: str = "r",
        *args: object,
        **kwargs: object,
    ) -> RecordingStream:
        stream = original_open(path, mode, *args, **kwargs)
        return RecordingStream(stream)

    monkeypatch.setattr(Path, "open", recording_open)
    read_bounded = paper_continuous._read_bounded  # noqa: SLF001
    assert read_bounded(target, 10) == b"{}"
    assert requested == [11]


def test_runner_state_bounded_reader_normalizes_open_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "state.json"

    def broken_open(_path: Path, _mode: str = "r") -> object:
        raise OSError("secret path")

    monkeypatch.setattr(Path, "open", broken_open)
    read_bounded = paper_continuous._read_bounded  # noqa: SLF001
    with pytest.raises(PaperRunnerCorruptError):
        read_bounded(target, 10)


def test_state_store_normalizes_unknown_persisted_enums(tmp_path: Path) -> None:
    state = _runner_state("a" * 64, "b" * 64)
    store = PaperRunnerStateStore(tmp_path)
    payload = paper_runner_state_payload(state)
    payload["status"] = "UNKNOWN"
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))

    with pytest.raises(PaperRunnerCorruptError):
        store.read()


def test_state_store_require_missing_uses_specific_error(tmp_path: Path) -> None:
    with pytest.raises(PaperRunnerStateNotFoundError):
        PaperRunnerStateStore(tmp_path).require()


@pytest.mark.asyncio
async def test_runner_executes_bounded_cycles_and_publishes_latest(tmp_path: Path) -> None:
    _, config, state = _state(tmp_path / "session")
    session_id = paper_session_id(config)
    stub = StubPaperTradingService({session_id: PaperRunResult(PaperRunAction.NOOP, state)})
    service = PaperTradingContinuousService(
        stub,
        policy=PaperRunnerPolicy(1, 10),
        clock=lambda: datetime(2026, 8, 3, tzinfo=UTC),
    )
    store = PaperRunnerStateStore(tmp_path / "runner")
    sleeps: list[float] = []

    async def sleeper(delay: float) -> None:
        sleeps.append(delay)

    runner = PaperTradingContinuousRunner(
        service=service,
        state_store=store,
        lock_manager=DatasetLockManager(
            tmp_path / "runner",
            timeout_seconds=1,
            stale_after_seconds=60,
        ),
        sleeper=sleeper,
        clock=lambda: datetime(2026, 8, 3, tzinfo=UTC),
    )
    result = await runner.run((session_id,), max_cycles=2)

    assert result.cycle_index == 2
    assert store.require() == result
    assert stub.calls == [session_id, session_id]
    assert sleeps == [1.0]


def test_repository_lists_verified_sessions_and_ignores_runner(tmp_path: Path) -> None:
    repository = PaperTradingRepository(tmp_path)
    config = _config()
    session_id = paper_session_id(config)
    repository.create(config)
    PaperRunnerStateStore(tmp_path).write(_runner_state(session_id, "b" * 64))
    assert repository.list_session_ids() == (session_id,)


def test_read_service_lists_slices_orders_and_fills(tmp_path: Path) -> None:
    service, config, state = _state(tmp_path)
    read = PaperTradingReadService(service._repository)  # noqa: SLF001
    session_id = paper_session_id(config)

    page = read.list_sessions(page=1, page_size=10)
    detail = read.get_session(session_id)
    orders = read.list_orders(session_id, page=1, page_size=10)
    fills = read.list_fills(session_id, page=1, page_size=10)

    assert page.total == 1
    assert detail.state == state
    assert orders.total == len(state.orders)
    assert fills.total == len(state.fills)


def test_cli_parser_exposes_runner_commands() -> None:
    args = build_parser().parse_args(
        [
            "paper-trading",
            "runner",
            "loop",
            "--session-id",
            "a" * 64,
            "--interval-seconds",
            "15",
            "--max-cycles",
            "2",
            "--yes",
        ]
    )
    assert args.paper_command == "runner"
    assert args.runner_command == "loop"
    assert args.session_id == ["a" * 64]


class FakeRunnerStore:
    def __init__(self, state: PaperRunnerState | None) -> None:
        self.state = state

    def require(self) -> PaperRunnerState:
        if self.state is None:
            raise PaperRunnerStateNotFoundError()
        return self.state


@pytest_asyncio.fixture
async def paper_api_client(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    service, config, state = _state(tmp_path)
    session_id = paper_session_id(config)
    read = PaperTradingReadService(service._repository)  # noqa: SLF001
    runner_state = _runner_state(session_id, state.state_id)
    app.dependency_overrides[get_paper_trading_read_service] = lambda: read
    app.dependency_overrides[get_paper_runner_state_store] = lambda: FakeRunnerStore(runner_state)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_read_only_api_exposes_runner_sessions_orders_and_fills(
    paper_api_client: httpx.AsyncClient,
) -> None:
    sessions = await paper_api_client.get("/api/v1/paper-trading/sessions")
    session_id = sessions.json()["items"][0]["session_id"]
    detail = await paper_api_client.get(f"/api/v1/paper-trading/sessions/{session_id}")
    orders = await paper_api_client.get(f"/api/v1/paper-trading/sessions/{session_id}/orders")
    fills = await paper_api_client.get(f"/api/v1/paper-trading/sessions/{session_id}/fills")
    runner = await paper_api_client.get("/api/v1/paper-trading/runner/status")

    assert sessions.status_code == detail.status_code == 200
    assert orders.status_code == fills.status_code == runner.status_code == 200
    assert sessions.json()["items"][0]["initial_capital"] == "1000"
    assert detail.json()["summary"]["state_id"] is not None
    assert orders.json()["total"] == 1
    assert runner.json()["status"] == "COMPLETED"
    assert isinstance(json.loads(detail.text)["summary"]["portfolio"]["equity"], str)


def test_paper_trading_http_boundary_is_read_only() -> None:
    methods = {
        method
        for route in paper_trading_routes.router.routes
        for method in (getattr(route, "methods", None) or set())
    }
    assert methods == {"GET"}


@pytest.mark.asyncio
async def test_runner_status_missing_uses_stable_404(
    paper_api_client: httpx.AsyncClient,
) -> None:
    app.dependency_overrides[get_paper_runner_state_store] = lambda: FakeRunnerStore(None)
    response = await paper_api_client.get("/api/v1/paper-trading/runner/status")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "paper_runner_state_not_found"


def test_runner_state_payload_is_canonical_and_complete() -> None:
    state = _runner_state("a" * 64, "b" * 64)
    payload = paper_runner_state_payload(state)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    assert payload["cycle_id"] == state.cycle_id
    assert payload["checksum"] == state.checksum
    assert " " not in encoded
