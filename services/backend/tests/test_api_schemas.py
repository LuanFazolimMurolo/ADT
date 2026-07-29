"""Unit tests for explicit Phase 1B API contracts."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.schemas import (
    CapitalMovementResponse,
    CapitalMovementType,
    MovementCreateRequest,
    PageMeta,
    PageParams,
    PublicSimulationSummaryResponse,
    SettingPatchRequest,
    SimulationCreateRequest,
    SimulationDetailResponse,
    SimulationListItem,
)
from app.domain.models import SimulationDetails, SimulationRun, SimulationStatus


def test_simulation_create_strips_text_and_preserves_decimal_in_json() -> None:
    request = SimulationCreateRequest.model_validate(
        {
            "name": "  Paper BRL  ",
            "initial_capital": "100000.12345678",
            "currency": "  BRL ",
        },
    )

    assert request.name == "Paper BRL"
    assert request.currency == "BRL"
    assert request.initial_capital == Decimal("100000.12345678")
    assert json.loads(request.model_dump_json())["initial_capital"] == "100000.12345678"


@pytest.mark.parametrize(
    "capital",
    ["0", "-1", "NaN", "Infinity", "-Infinity", "1.123456789", "1000000000000"],
)
def test_simulation_create_rejects_invalid_capital(capital: str) -> None:
    with pytest.raises(ValidationError):
        SimulationCreateRequest.model_validate(
            {"name": "Simulation", "initial_capital": capital, "currency": "BRL"},
        )


@pytest.mark.parametrize(
    ("movement_type", "amount"),
    [
        ("DEPOSIT", "10"),
        ("WITHDRAWAL", "-10"),
        ("ADJUSTMENT", "10"),
        ("ADJUSTMENT", "-10"),
    ],
)
def test_movement_create_accepts_only_valid_signs(
    movement_type: str,
    amount: str,
) -> None:
    request = MovementCreateRequest.model_validate(
        {
            "type": movement_type,
            "amount": amount,
            "reason": "  administrative correction  ",
            "metadata": {"ticket": "ADT-1"},
        },
    )

    assert request.amount == Decimal(amount)
    assert request.reason == "administrative correction"


@pytest.mark.parametrize(
    ("movement_type", "amount"),
    [
        ("DEPOSIT", "-1"),
        ("DEPOSIT", "0"),
        ("WITHDRAWAL", "1"),
        ("WITHDRAWAL", "0"),
        ("ADJUSTMENT", "0"),
        ("INITIAL_CAPITAL", "100"),
    ],
)
def test_movement_create_rejects_invalid_or_internal_movement(
    movement_type: str,
    amount: str,
) -> None:
    with pytest.raises(ValidationError):
        MovementCreateRequest.model_validate(
            {"type": movement_type, "amount": amount, "reason": "reason"},
        )


def test_movement_response_serializes_decimal_as_string() -> None:
    response = CapitalMovementResponse(
        id=uuid4(),
        simulation_id=uuid4(),
        type=CapitalMovementType.INITIAL_CAPITAL,
        amount=Decimal("50.00000000"),
        reason="Opening capital",
        reference_id=None,
        created_by=uuid4(),
        created_at=datetime.now(UTC),
    )

    assert json.loads(response.model_dump_json())["amount"] == "50.00000000"


def test_public_summary_has_no_identifier_field() -> None:
    summary = PublicSimulationSummaryResponse.model_validate(
        {
            "simulation_name": "Public simulation",
            "currency": "BRL",
            "initial_capital": "100",
            "current_balance": "110",
            "total_profit_loss": "10",
            "started_at": datetime.now(UTC),
            "status": "ACTIVE",
        },
    )

    payload = summary.model_dump(mode="json")
    assert payload["name"] == "Public simulation"
    assert "id" not in payload
    assert "simulation_id" not in payload


def test_setting_patch_rejects_key_changes() -> None:
    with pytest.raises(ValidationError):
        SettingPatchRequest.model_validate({"key": "other-key", "value": True})


def test_page_params_are_bounded_and_expose_repository_offset() -> None:
    page = PageParams(page=3, page_size=25)

    assert page.offset == 50
    with pytest.raises(ValidationError):
        PageParams(page=1, page_size=101)


def test_page_meta_calculates_total_pages() -> None:
    metadata = PageMeta.from_total(page=2, page_size=25, total=51)

    assert metadata.total_pages == 3


def test_simulation_responses_flatten_domain_details() -> None:
    now = datetime.now(UTC)
    simulation = SimulationRun(
        id=uuid4(),
        name="Simulation",
        status=SimulationStatus.ACTIVE,
        currency="BRL",
        initial_capital=Decimal("100.00000000"),
        started_at=now,
        ended_at=None,
        created_by=uuid4(),
        created_at=now,
        updated_at=now,
    )
    details = SimulationDetails(
        simulation=simulation,
        current_balance=Decimal("105.50000000"),
        total_profit_loss=Decimal("5.50000000"),
    )

    list_item = SimulationListItem.from_domain(details)
    detail = SimulationDetailResponse.from_domain(details)

    assert list_item.id == simulation.id
    assert detail.created_by == simulation.created_by
    assert detail.current_balance == Decimal("105.50000000")
