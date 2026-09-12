"""Pure domain contracts for official operational paper-session settlements."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, InvalidOperation, localcontext
from typing import Final
from uuid import UUID

from app.backtesting.domain import PortfolioSnapshot
from app.backtesting.serialization import canonical_json_bytes, decimal_text
from app.operational_paper_capital_authorizations.domain import (
    OperationalPaperCapitalAuthorization,
    OperationalPaperCapitalAuthorizationState,
)
from app.operational_paper_capital_eras.domain import OperationalPaperCapitalEra
from app.operational_paper_session_runs.domain import (
    OperationalPaperSessionRunDesiredState,
    OperationalPaperSessionRunEpoch,
    OperationalPaperSessionRunObservedState,
)
from app.operational_paper_session_settlements.errors import (
    InvalidOperationalPaperSessionSettlementSpecificationError,
    OperationalPaperSessionSettlementBoundsExceededError,
    OperationalPaperSessionSettlementChecksumMismatchError,
    OperationalPaperSessionSettlementEligibilityConflictError,
)
from app.paper_trading.persisted_state import PaperPersistedStateBinding

OPERATIONAL_PAPER_SESSION_SETTLEMENT_SCHEMA_VERSION: Final = 1
OPERATIONAL_PAPER_SESSION_SETTLEMENT_CONTRACT_VERSION: Final = 1

MAX_OPERATIONAL_PAPER_SESSION_SETTLEMENT_IDEMPOTENCY_KEY_LENGTH: Final = 128

OPERATIONAL_PAPER_SESSION_SETTLEMENT_QUANTUM: Final = Decimal("0.00000001")
MAX_OPERATIONAL_PAPER_SESSION_SETTLEMENT_AMOUNT: Final = Decimal("999999999999.99999999")

_SAFE_TOKEN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")


def _require_uuid(value: object) -> UUID:
    if not isinstance(value, UUID) or value.int == 0:
        raise ValueError
    return value


def _require_sha256(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError
    return value


def _require_utc(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError
    return value.astimezone(UTC)


def validate_operational_paper_session_settlement_idempotency_key(
    value: object,
) -> str:
    if not isinstance(value, str):
        raise InvalidOperationalPaperSessionSettlementSpecificationError()

    if not (1 <= len(value) <= MAX_OPERATIONAL_PAPER_SESSION_SETTLEMENT_IDEMPOTENCY_KEY_LENGTH):
        raise OperationalPaperSessionSettlementBoundsExceededError()

    if _SAFE_TOKEN.fullmatch(value) is None:
        raise InvalidOperationalPaperSessionSettlementSpecificationError()

    return value


# Fixed precision isolates financial validation from a caller's Decimal context.
# numeric(20,8) subtraction needs at most 21 significant digits.
_FINANCIAL_FIELDS: Final = (
    "initial_capital",
    "final_quote_cash",
    "realized_pnl",
    "unrealized_pnl",
    "base_quantity",
    "average_entry_price",
    "cost_basis",
    "total_fees",
    "total_slippage_cost",
    "settlement_delta",
)


def _canonical_amount(value: object) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        raise ValueError
    if value.copy_abs() > MAX_OPERATIONAL_PAPER_SESSION_SETTLEMENT_AMOUNT:
        raise ValueError
    try:
        with localcontext(Context(prec=40)):
            persisted = value.quantize(OPERATIONAL_PAPER_SESSION_SETTLEMENT_QUANTUM)
    except InvalidOperation:
        raise ValueError from None
    if persisted != value:
        raise ValueError
    return Decimal(decimal_text(persisted))


def _exact_delta(final_quote_cash: Decimal, initial_capital: Decimal) -> Decimal:
    with localcontext(Context(prec=40)):
        return final_quote_cash - initial_capital


@dataclass(frozen=True, slots=True)
class OperationalPaperSessionSettlementIntent:
    """Administrator intent contains identity only, never financial amounts."""

    epoch_id: UUID
    epoch_checksum: str

    def __post_init__(self) -> None:
        try:
            _require_uuid(self.epoch_id)
            _require_sha256(self.epoch_checksum)
        except Exception:
            raise InvalidOperationalPaperSessionSettlementSpecificationError() from None


def _revalidate_intent(value: object) -> OperationalPaperSessionSettlementIntent:
    if not isinstance(value, OperationalPaperSessionSettlementIntent):
        raise InvalidOperationalPaperSessionSettlementSpecificationError()
    return OperationalPaperSessionSettlementIntent(
        epoch_id=value.epoch_id,
        epoch_checksum=value.epoch_checksum,
    )


def operational_paper_session_settlement_intent_fingerprint(
    intent: OperationalPaperSessionSettlementIntent,
) -> str:
    canonical = _revalidate_intent(intent)
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "contract_version": OPERATIONAL_PAPER_SESSION_SETTLEMENT_CONTRACT_VERSION,
                "epoch_id": str(canonical.epoch_id),
                "epoch_checksum": canonical.epoch_checksum,
            }
        )
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class OperationalPaperSessionSettlementSpecification:
    """Terminal evidence; the epoch checksum seals the existing provenance chain.

    The optional movement identity is a link to the sole existing capital ledger.
    This specification is not a balance, event ledger or mutable lifecycle.
    """

    schema_version: int
    settlement_contract_version: int
    era_id: UUID
    era_checksum: str
    simulation_id: UUID
    epoch_id: UUID
    epoch_checksum: str
    epoch_terminal_at: datetime
    authorization_id: UUID
    authorization_checksum: str
    session_id: str
    config_checksum: str
    state_id: str
    state_checksum: str
    dataset_version: str
    source_checksum: str
    timeline_id: str
    timeline_content_checksum: str
    initial_capital: Decimal
    final_quote_cash: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    base_quantity: Decimal
    average_entry_price: Decimal
    cost_basis: Decimal
    total_fees: Decimal
    total_slippage_cost: Decimal
    settlement_delta: Decimal
    ledger_movement_id: UUID | None

    def __post_init__(self) -> None:
        try:
            if (
                type(self.schema_version) is not int
                or self.schema_version != OPERATIONAL_PAPER_SESSION_SETTLEMENT_SCHEMA_VERSION
                or type(self.settlement_contract_version) is not int
                or self.settlement_contract_version
                != OPERATIONAL_PAPER_SESSION_SETTLEMENT_CONTRACT_VERSION
            ):
                raise ValueError
            for identity in (self.era_id, self.simulation_id, self.epoch_id, self.authorization_id):
                _require_uuid(identity)
            for checksum in (self.era_checksum, self.epoch_checksum, self.authorization_checksum):
                _require_sha256(checksum)
            # Reuse the existing exact eight-dimensional persisted-state contract.
            PaperPersistedStateBinding(
                session_id=self.session_id,
                config_checksum=self.config_checksum,
                state_id=self.state_id,
                state_checksum=self.state_checksum,
                dataset_version=self.dataset_version,
                source_checksum=self.source_checksum,
                timeline_id=self.timeline_id,
                timeline_content_checksum=self.timeline_content_checksum,
            )
            terminal_at = _require_utc(self.epoch_terminal_at)
            amounts = {name: _canonical_amount(getattr(self, name)) for name in _FINANCIAL_FIELDS}
            if amounts["initial_capital"] <= 0 or any(
                amounts[name] < 0
                for name in (
                    "final_quote_cash",
                    "base_quantity",
                    "average_entry_price",
                    "cost_basis",
                    "total_fees",
                    "total_slippage_cost",
                )
            ):
                raise ValueError
            if any(
                amounts[name] != 0
                for name in ("base_quantity", "average_entry_price", "cost_basis", "unrealized_pnl")
            ):
                raise OperationalPaperSessionSettlementEligibilityConflictError()
            delta = amounts["settlement_delta"]
            if (
                delta != _exact_delta(amounts["final_quote_cash"], amounts["initial_capital"])
                or delta != amounts["realized_pnl"]
            ):
                raise ValueError
            if delta == 0:
                if self.ledger_movement_id is not None:
                    raise ValueError
            else:
                _require_uuid(self.ledger_movement_id)
        except OperationalPaperSessionSettlementEligibilityConflictError:
            raise
        except Exception:
            raise InvalidOperationalPaperSessionSettlementSpecificationError() from None
        object.__setattr__(self, "epoch_terminal_at", terminal_at)
        for name, amount in amounts.items():
            object.__setattr__(self, name, amount)


def _revalidate_specification(value: object) -> OperationalPaperSessionSettlementSpecification:
    if not isinstance(value, OperationalPaperSessionSettlementSpecification):
        raise InvalidOperationalPaperSessionSettlementSpecificationError()
    return OperationalPaperSessionSettlementSpecification(
        **{
            field.name: getattr(value, field.name)
            for field in fields(OperationalPaperSessionSettlementSpecification)
        }
    )


def operational_paper_session_settlement_specification_payload(
    specification: OperationalPaperSessionSettlementSpecification,
) -> dict[str, object]:
    canonical = _revalidate_specification(specification)
    payload: dict[str, object] = {}
    for field in fields(OperationalPaperSessionSettlementSpecification):
        value = getattr(canonical, field.name)
        payload[field.name] = (
            str(value)
            if isinstance(value, UUID)
            else value.isoformat()
            if isinstance(value, datetime)
            else value
        )
    return payload


def operational_paper_session_settlement_specification_bytes(
    specification: OperationalPaperSessionSettlementSpecification,
) -> bytes:
    return canonical_json_bytes(
        operational_paper_session_settlement_specification_payload(specification)
    )


def operational_paper_session_settlement_specification_checksum(
    specification: OperationalPaperSessionSettlementSpecification,
) -> str:
    return hashlib.sha256(
        operational_paper_session_settlement_specification_bytes(specification)
    ).hexdigest()


def validate_operational_paper_session_settlement_specification_checksum(
    specification: OperationalPaperSessionSettlementSpecification,
    expected_checksum: object,
) -> OperationalPaperSessionSettlementSpecification:
    canonical = _revalidate_specification(specification)
    try:
        checksum = _require_sha256(expected_checksum)
    except Exception:
        raise InvalidOperationalPaperSessionSettlementSpecificationError() from None
    if operational_paper_session_settlement_specification_checksum(canonical) != checksum:
        raise OperationalPaperSessionSettlementChecksumMismatchError()
    return canonical


def operational_paper_session_settlement_specifications_equal(
    left: OperationalPaperSessionSettlementSpecification,
    right: OperationalPaperSessionSettlementSpecification,
) -> bool:
    return operational_paper_session_settlement_specification_bytes(
        left
    ) == operational_paper_session_settlement_specification_bytes(right)


def build_operational_paper_session_settlement_specification(
    intent: OperationalPaperSessionSettlementIntent,
    *,
    era: OperationalPaperCapitalEra,
    epoch: OperationalPaperSessionRunEpoch,
    authorization: OperationalPaperCapitalAuthorization,
    persisted_state_binding: PaperPersistedStateBinding,
    initial_capital: Decimal,
    portfolio: PortfolioSnapshot,
    ledger_movement_id: UUID | None,
) -> OperationalPaperSessionSettlementSpecification:
    """Bind supplied authoritative evidence without I/O or authority mutation.

    Gate 2D must verify config/state/portfolio and the persisted binding together
    before calling this helper. Gate 2C must recheck mutable PostgreSQL authority
    and commit the reservation release, optional movement and settlement atomically.
    A pure value cannot prove storage authenticity or global session uniqueness.
    """
    canonical_intent = _revalidate_intent(intent)
    try:
        if (
            not isinstance(era, OperationalPaperCapitalEra)
            or not isinstance(epoch, OperationalPaperSessionRunEpoch)
            or not isinstance(authorization, OperationalPaperCapitalAuthorization)
            or not isinstance(persisted_state_binding, PaperPersistedStateBinding)
            or not isinstance(portfolio, PortfolioSnapshot)
        ):
            raise ValueError
        # Reconstruct through public constructors; never trust frozen instances
        # alone at a boundary where callers may have bypassed __post_init__.
        era = replace(era)
        epoch = replace(epoch)
        authorization = replace(authorization)
        binding = replace(persisted_state_binding)
        portfolio = replace(portfolio)
        capital = _canonical_amount(initial_capital)
        final_quote_cash = _canonical_amount(portfolio.quote_cash)
    except Exception:
        raise InvalidOperationalPaperSessionSettlementSpecificationError() from None

    if (
        epoch.desired_state is not OperationalPaperSessionRunDesiredState.STOPPED
        or epoch.observed_state is not OperationalPaperSessionRunObservedState.STOPPED
        or epoch.terminal_at is None
        or authorization.state is not OperationalPaperCapitalAuthorizationState.AUTHORIZED
        or canonical_intent.epoch_id != epoch.epoch_id
        or canonical_intent.epoch_checksum != epoch.epoch_checksum
        or era.simulation_id != epoch.simulation_id
        or authorization.simulation_id != epoch.simulation_id
        or authorization.authorization_id != epoch.authorization_binding.authorization_id
        or authorization.authorization_checksum
        != epoch.authorization_binding.authorization_checksum
        or authorization.profile_binding.profile_id != epoch.profile_binding.profile_id
        or authorization.profile_binding.approved_revision
        != epoch.profile_binding.approved_revision
        or authorization.profile_binding.specification_checksum
        != epoch.profile_binding.specification_checksum
        or authorization.quote_asset != era.currency
        or capital != authorization.authorized_capital
        or binding.session_id != epoch.session_id
        or binding.config_checksum != epoch.config_checksum
        or era.designated_at > authorization.created_at
        or authorization.created_at > epoch.start_requested_at
    ):
        raise OperationalPaperSessionSettlementEligibilityConflictError()

    return OperationalPaperSessionSettlementSpecification(
        schema_version=OPERATIONAL_PAPER_SESSION_SETTLEMENT_SCHEMA_VERSION,
        settlement_contract_version=OPERATIONAL_PAPER_SESSION_SETTLEMENT_CONTRACT_VERSION,
        era_id=era.era_id,
        era_checksum=era.era_checksum,
        simulation_id=epoch.simulation_id,
        epoch_id=epoch.epoch_id,
        epoch_checksum=epoch.epoch_checksum,
        epoch_terminal_at=epoch.terminal_at,
        authorization_id=authorization.authorization_id,
        authorization_checksum=authorization.authorization_checksum,
        session_id=binding.session_id,
        config_checksum=binding.config_checksum,
        state_id=binding.state_id,
        state_checksum=binding.state_checksum,
        dataset_version=binding.dataset_version,
        source_checksum=binding.source_checksum,
        timeline_id=binding.timeline_id,
        timeline_content_checksum=binding.timeline_content_checksum,
        initial_capital=capital,
        final_quote_cash=portfolio.quote_cash,
        realized_pnl=portfolio.realized_pnl,
        unrealized_pnl=portfolio.unrealized_pnl,
        base_quantity=portfolio.base_quantity,
        average_entry_price=portfolio.average_entry_price,
        cost_basis=portfolio.cost_basis,
        total_fees=portfolio.total_fees,
        total_slippage_cost=portfolio.total_slippage_cost,
        settlement_delta=_exact_delta(final_quote_cash, capital),
        ledger_movement_id=ledger_movement_id,
    )


@dataclass(frozen=True, slots=True)
class OperationalPaperSessionSettlement:
    """Immutable terminal evidence with actor-scoped replay metadata."""

    settlement_id: UUID
    specification: OperationalPaperSessionSettlementSpecification
    settlement_checksum: str
    settled_by: UUID
    settled_at: datetime
    settle_idempotency_key: str
    settle_intent_fingerprint: str

    def __post_init__(self) -> None:
        try:
            _require_uuid(self.settlement_id)
            specification = validate_operational_paper_session_settlement_specification_checksum(
                self.specification, self.settlement_checksum
            )
            _require_uuid(self.settled_by)
            settled_at = _require_utc(self.settled_at)
            if settled_at < specification.epoch_terminal_at:
                raise ValueError
            key = validate_operational_paper_session_settlement_idempotency_key(
                self.settle_idempotency_key
            )
            fingerprint = _require_sha256(self.settle_intent_fingerprint)
            expected = operational_paper_session_settlement_intent_fingerprint(
                OperationalPaperSessionSettlementIntent(
                    epoch_id=specification.epoch_id,
                    epoch_checksum=specification.epoch_checksum,
                )
            )
            if fingerprint != expected:
                raise ValueError
        except (
            OperationalPaperSessionSettlementBoundsExceededError,
            OperationalPaperSessionSettlementChecksumMismatchError,
            OperationalPaperSessionSettlementEligibilityConflictError,
        ):
            raise
        except Exception:
            raise InvalidOperationalPaperSessionSettlementSpecificationError() from None
        object.__setattr__(self, "specification", specification)
        object.__setattr__(self, "settled_at", settled_at)
        object.__setattr__(self, "settle_idempotency_key", key)
        object.__setattr__(self, "settle_intent_fingerprint", fingerprint)
