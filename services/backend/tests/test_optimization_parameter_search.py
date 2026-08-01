"""Phase 4-01 deterministic finite parameter-search tests."""

from __future__ import annotations

import copy
import sys
from dataclasses import FrozenInstanceError, dataclass, replace
from decimal import Decimal, localcontext
from pathlib import Path

import pytest

from app.backtesting.domain import StrategyDescriptor, StrategyParameters
from app.backtesting.strategy import NoOpStrategy
from app.optimization import (
    ABSOLUTE_MAX_COMBINATIONS,
    DEFAULT_MAX_COMBINATIONS,
    MAX_CANONICAL_INTEGER_DIGITS,
    DuplicateSearchParameterValueError,
    EmptyParameterSearchSpaceError,
    EmptySearchParameterValuesError,
    FixedParameter,
    IncompatibleSearchParameterTypeError,
    IncompatibleSearchSpaceDocumentError,
    InvalidCombinationLimitError,
    InvalidSearchCombinationError,
    ParameterSearchService,
    ParameterSearchSpace,
    SearchCardinalityExceededError,
    SearchParameter,
    SearchParameterConflictError,
    SearchSpaceChecksumError,
    UnknownSearchParameterError,
    UnsupportedSearchSpaceSchemaError,
    canonical_document_bytes,
    to_document,
)
from app.optimization.canonical import canonical_decimal_length, decimal_text, integer_text
from app.strategies.domain import (
    StrategyParameterKind,
    StrategyParameterSpec,
    StrategyPluginDescriptor,
)
from app.strategies.errors import StrategyParameterValidationError
from app.strategies.registry import StrategyPluginRegistry


def _service() -> ParameterSearchService:
    return ParameterSearchService()


def _valid_space(
    *,
    max_combinations: int = DEFAULT_MAX_COMBINATIONS,
) -> ParameterSearchSpace:
    return _service().create(
        "ema-cross-example",
        "1",
        {"fast_period": [2, 3]},
        fixed_parameters={"slow_period": 5, "quantity": Decimal("0.1")},
        max_combinations=max_combinations,
    )


def test_valid_space_with_one_parameter_expands_full_normalized_parameters() -> None:
    service = _service()
    space = service.create(
        "ema-cross-example",
        "1",
        {"fast_period": [2, 3]},
        fixed_parameters={"slow_period": 5, "quantity": Decimal("0.1")},
    )

    expansion = service.expand(space)

    assert space.cardinality == 2
    assert [item.index for item in expansion.combinations] == [0, 1]
    assert expansion.combinations[0].parameters == (
        ("fast_period", 2),
        ("quantity", Decimal("0.1")),
        ("slow_period", 5),
    )
    assert expansion.rejected_combinations == 0


def test_valid_space_with_multiple_parameters_has_deterministic_cartesian_order() -> None:
    service = _service()
    space = service.create(
        "ema-cross-example",
        "1",
        {
            "quantity": [Decimal("0.2"), Decimal("0.1")],
            "fast_period": [3, 2],
        },
        fixed_parameters={"slow_period": 5},
    )

    combinations = service.expand(space).combinations

    assert space.cardinality == 4
    assert [dict(item.parameters) for item in combinations] == [
        {"fast_period": 2, "quantity": Decimal("0.1"), "slow_period": 5},
        {"fast_period": 2, "quantity": Decimal("0.2"), "slow_period": 5},
        {"fast_period": 3, "quantity": Decimal("0.1"), "slow_period": 5},
        {"fast_period": 3, "quantity": Decimal("0.2"), "slow_period": 5},
    ]


def test_mapping_and_value_input_order_do_not_change_canonical_space() -> None:
    first = _service().create(
        "ema-cross-example",
        "1",
        {"quantity": [Decimal("0.2"), Decimal("0.1")], "fast_period": [3, 2]},
        fixed_parameters={"slow_period": 5},
    )
    second = _service().create(
        "ema-cross-example",
        "1",
        {"fast_period": [2, 3], "quantity": [Decimal("0.1"), Decimal("0.2")]},
        fixed_parameters={"slow_period": 5},
    )

    assert first == second
    assert canonical_document_bytes(first) == canonical_document_bytes(second)


def test_fixed_parameters_participate_in_document_checksum_and_identifier() -> None:
    first = _service().create(
        "ema-cross-example",
        "1",
        {"fast_period": [2]},
        fixed_parameters={"slow_period": 5, "quantity": Decimal("0.1")},
    )
    second = _service().create(
        "ema-cross-example",
        "1",
        {"fast_period": [2]},
        fixed_parameters={"slow_period": 5, "quantity": Decimal("0.2")},
    )

    assert first.checksum != second.checksum
    assert first.search_space_id != second.search_space_id


def test_fixed_and_searchable_parameter_conflict_is_rejected() -> None:
    with pytest.raises(SearchParameterConflictError, match="fast_period"):
        _service().create(
            "ema-cross-example",
            "1",
            {"fast_period": [2]},
            fixed_parameters={
                "fast_period": 2,
                "slow_period": 5,
                "quantity": Decimal("0.1"),
            },
        )


@pytest.mark.parametrize("location", ["search", "fixed"])
def test_unknown_parameter_is_rejected(location: str) -> None:
    search: dict[str, list[object]] = {"fast_period": [2]}
    fixed: dict[str, object] = {"slow_period": 5, "quantity": Decimal("0.1")}
    if location == "search":
        search["unknown"] = [1]
    else:
        fixed["unknown"] = 1

    with pytest.raises(UnknownSearchParameterError, match="unknown"):
        _service().create(
            "ema-cross-example",
            "1",
            search,
            fixed_parameters=fixed,
        )


@pytest.mark.parametrize("value", [2.0, True, None, [2], {"period": 2}])
def test_incompatible_and_mutable_values_are_rejected(value: object) -> None:
    with pytest.raises(IncompatibleSearchParameterTypeError):
        _service().create(
            "ema-cross-example",
            "1",
            {"fast_period": [value]},
            fixed_parameters={"slow_period": 5, "quantity": Decimal("0.1")},
        )


def test_generator_is_not_accepted_as_a_finite_value_sequence() -> None:
    values = (value for value in (2, 3))
    with pytest.raises(IncompatibleSearchParameterTypeError, match="finite sequence"):
        _service().create(
            "ema-cross-example",
            "1",
            {"fast_period": values},
            fixed_parameters={"slow_period": 5, "quantity": Decimal("0.1")},
        )


@pytest.mark.parametrize(
    "values",
    [
        [Decimal("1.0"), Decimal("1.00")],
        [Decimal("1"), Decimal("1")],
    ],
)
def test_duplicate_values_after_decimal_canonicalization_are_rejected(
    values: list[Decimal],
) -> None:
    with pytest.raises(DuplicateSearchParameterValueError):
        _service().create(
            "ema-cross-example",
            "1",
            {"quantity": values},
            fixed_parameters={"fast_period": 2, "slow_period": 5},
        )


def test_empty_values_and_empty_search_space_are_rejected() -> None:
    with pytest.raises(EmptySearchParameterValuesError):
        _service().create(
            "ema-cross-example",
            "1",
            {"fast_period": []},
            fixed_parameters={"slow_period": 5, "quantity": Decimal("0.1")},
        )
    with pytest.raises(EmptyParameterSearchSpaceError):
        _service().create(
            "ema-cross-example",
            "1",
            {},
            fixed_parameters={"slow_period": 5, "quantity": Decimal("0.1")},
        )


@pytest.mark.parametrize("maximum", [0, -1, True, ABSOLUTE_MAX_COMBINATIONS + 1])
def test_invalid_combination_limits_are_rejected(maximum: int) -> None:
    with pytest.raises(InvalidCombinationLimitError):
        _service().create(
            "ema-cross-example",
            "1",
            {"fast_period": [2]},
            fixed_parameters={"slow_period": 5, "quantity": Decimal("0.1")},
            max_combinations=maximum,
        )


def test_cardinality_limit_is_checked_before_any_factory_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service()
    called = False

    def unexpected_build(*args: object, **kwargs: object) -> NoOpStrategy:
        nonlocal called
        called = True
        raise AssertionError("factory must not run")

    monkeypatch.setattr(service._registry, "build", unexpected_build)

    with pytest.raises(SearchCardinalityExceededError):
        service.create(
            "ema-cross-example",
            "1",
            {"fast_period": [1, 2], "quantity": [Decimal("0.1"), Decimal("0.2")]},
            fixed_parameters={"slow_period": 5},
            max_combinations=3,
        )
    assert called is False


def test_factory_rejects_entire_space_at_first_invalid_combination() -> None:
    with pytest.raises(InvalidSearchCombinationError, match="combination 1.*fast < slow"):
        _service().create(
            "ema-cross-example",
            "1",
            {"fast_period": [2, 5]},
            fixed_parameters={"slow_period": 5, "quantity": Decimal("0.1")},
        )


def test_decimal_with_more_than_28_digits_round_trips_exactly() -> None:
    value = Decimal("1234567890123456789012345678901234567890.123456789")
    service = _service()
    space = service.create(
        "ema-cross-example",
        "1",
        {"quantity": [value]},
        fixed_parameters={"fast_period": 2, "slow_period": 5},
    )

    restored = service.from_document(to_document(space))

    assert restored == space
    assert service.expand(restored).combinations[0].parameters[1][1] == value


def test_decimal_context_does_not_change_document_checksum_or_ids() -> None:
    value = Decimal("123456789012345678901234567890.1234500")
    with localcontext() as context:
        context.prec = 6
        first = _service().create(
            "ema-cross-example",
            "1",
            {"quantity": [value]},
            fixed_parameters={"fast_period": 2, "slow_period": 5},
        )
        first_expansion = _service().expand(first)
    with localcontext() as context:
        context.prec = 80
        second = _service().create(
            "ema-cross-example",
            "1",
            {"quantity": [value]},
            fixed_parameters={"fast_period": 2, "slow_period": 5},
        )
        second_expansion = _service().expand(second)

    assert first == second
    assert first_expansion == second_expansion


@pytest.mark.parametrize(
    ("value", "expected_length"),
    [
        (Decimal("1E+100000000"), 100_000_001),
        (Decimal("1E-100000000"), 100_000_002),
    ],
)
def test_extreme_decimal_exponents_are_sized_without_zero_padding(
    value: Decimal,
    expected_length: int,
) -> None:
    assert canonical_decimal_length(value) == expected_length
    with pytest.raises(IncompatibleSearchSpaceDocumentError, match="128 characters"):
        decimal_text(value)


def test_decimal_size_is_rejected_before_canonical_text_construction() -> None:
    value = Decimal("9E+1000")

    assert canonical_decimal_length(value) == 1_001
    with pytest.raises(IncompatibleSearchSpaceDocumentError, match="128 characters"):
        decimal_text(value)


def test_signed_decimal_zero_remains_canonical_and_lossless_values_remain_exact() -> None:
    assert decimal_text(Decimal("0")) == "0"
    assert decimal_text(Decimal("-0")) == "0"
    assert decimal_text(Decimal("1.2300")) == "1.23"


def test_integer_limit_precedes_python_configurable_conversion_limit() -> None:
    previous_limit = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(640)
        assert integer_text((10**MAX_CANONICAL_INTEGER_DIGITS) - 1) == (
            "9" * MAX_CANONICAL_INTEGER_DIGITS
        )
        with pytest.raises(IncompatibleSearchSpaceDocumentError, match="128 canonical digits"):
            integer_text(10**MAX_CANONICAL_INTEGER_DIGITS)
        with pytest.raises(IncompatibleSearchSpaceDocumentError, match="128 canonical digits"):
            integer_text(-(10**MAX_CANONICAL_INTEGER_DIGITS))
    finally:
        sys.set_int_max_str_digits(previous_limit)


def test_service_rejects_extreme_integer_before_factory_or_document_encoding() -> None:
    with pytest.raises(IncompatibleSearchParameterTypeError, match="128 canonical digits"):
        _service().create(
            "ema-cross-example",
            "1",
            {"fast_period": [10**MAX_CANONICAL_INTEGER_DIGITS]},
            fixed_parameters={"slow_period": 5, "quantity": Decimal("0.1")},
        )


@pytest.mark.parametrize(
    "search",
    [
        {1: [2]},
        {"fast_period": [2], 1: [3]},
    ],
)
def test_non_text_search_mapping_keys_have_a_stable_domain_error(
    search: dict[object, list[int]],
) -> None:
    with pytest.raises(UnknownSearchParameterError, match="names must be strings"):
        _service().create(
            "ema-cross-example",
            "1",
            search,
            fixed_parameters={"slow_period": 5, "quantity": Decimal("0.1")},
        )


@pytest.mark.parametrize(
    "fixed",
    [
        {1: 2, "slow_period": 5, "quantity": Decimal("0.1")},
        {
            "fast_period": 2,
            "slow_period": 5,
            "quantity": Decimal("0.1"),
            1: 3,
        },
    ],
)
def test_non_text_fixed_mapping_keys_have_a_stable_domain_error(
    fixed: dict[object, object],
) -> None:
    with pytest.raises(UnknownSearchParameterError, match="names must be strings"):
        _service().create(
            "ema-cross-example",
            "1",
            {"fast_period": [2]},
            fixed_parameters=fixed,
        )


def test_direct_fixed_parameter_contract_rejects_noncanonical_names_and_values() -> None:
    with pytest.raises(IncompatibleSearchSpaceDocumentError, match="canonical text"):
        FixedParameter(" period ", StrategyParameterKind.INTEGER, 2)
    with pytest.raises(IncompatibleSearchParameterTypeError):
        FixedParameter("period", StrategyParameterKind.INTEGER, True)
    with pytest.raises(IncompatibleSearchParameterTypeError):
        FixedParameter("enabled", StrategyParameterKind.BOOLEAN, 1)
    with pytest.raises(IncompatibleSearchParameterTypeError):
        FixedParameter("quantity", StrategyParameterKind.DECIMAL, 1)
    with pytest.raises(IncompatibleSearchParameterTypeError):
        FixedParameter("label", StrategyParameterKind.STRING, " label ")


def test_direct_search_parameter_contract_rejects_invalid_shape_and_type() -> None:
    with pytest.raises(IncompatibleSearchParameterTypeError, match="must be a tuple"):
        SearchParameter("period", StrategyParameterKind.INTEGER, [1, 2])
    with pytest.raises(EmptySearchParameterValuesError):
        SearchParameter("period", StrategyParameterKind.INTEGER, ())
    with pytest.raises(IncompatibleSearchParameterTypeError):
        SearchParameter("period", StrategyParameterKind.INTEGER, (True,))


def test_direct_search_parameter_rejects_duplicates_and_noncanonical_order() -> None:
    with pytest.raises(DuplicateSearchParameterValueError):
        SearchParameter(
            "quantity",
            StrategyParameterKind.DECIMAL,
            (Decimal("1.0"), Decimal("1.00")),
        )
    with pytest.raises(IncompatibleSearchParameterTypeError, match="canonical order"):
        SearchParameter("period", StrategyParameterKind.INTEGER, (2, 1))


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"schema_version": 2}, UnsupportedSearchSpaceSchemaError),
        ({"max_combinations": 0}, InvalidCombinationLimitError),
        (
            {"max_combinations": ABSOLUTE_MAX_COMBINATIONS + 1},
            InvalidCombinationLimitError,
        ),
        ({"cardinality": 0}, IncompatibleSearchSpaceDocumentError),
        ({"cardinality": 1}, IncompatibleSearchSpaceDocumentError),
        ({"max_combinations": 1}, SearchCardinalityExceededError),
        ({"search_parameters": ()}, EmptyParameterSearchSpaceError),
        ({"plugin_name": ""}, IncompatibleSearchSpaceDocumentError),
        ({"plugin_version": ""}, IncompatibleSearchSpaceDocumentError),
        ({"plugin_schema_version": True}, IncompatibleSearchSpaceDocumentError),
        ({"plugin_lifecycle_version": 0}, IncompatibleSearchSpaceDocumentError),
        ({"cardinality": True}, IncompatibleSearchSpaceDocumentError),
        ({"combination_policy": "REJECT_SPACE"}, IncompatibleSearchSpaceDocumentError),
        ({"checksum": "invalid"}, IncompatibleSearchSpaceDocumentError),
        ({"search_space_id": "invalid"}, IncompatibleSearchSpaceDocumentError),
    ],
)
def test_direct_search_space_construction_rejects_structural_invalidity(
    changes: dict[str, object],
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        replace(_valid_space(), **changes)


def test_direct_search_space_rejects_non_tuple_dimensions_and_overlap() -> None:
    base = _valid_space()
    with pytest.raises(IncompatibleSearchSpaceDocumentError, match="must be a tuple"):
        replace(base, search_parameters=list(base.search_parameters))
    with pytest.raises(IncompatibleSearchSpaceDocumentError, match="must be a tuple"):
        replace(base, fixed_parameters=list(base.fixed_parameters))

    overlapping = (
        FixedParameter("fast_period", StrategyParameterKind.INTEGER, 1),
        *base.fixed_parameters,
    )
    with pytest.raises(SearchParameterConflictError, match="fast_period"):
        replace(base, fixed_parameters=overlapping)

    with pytest.raises(IncompatibleSearchSpaceDocumentError, match="must be unique"):
        replace(base, fixed_parameters=base.fixed_parameters + (base.fixed_parameters[0],))
    with pytest.raises(IncompatibleSearchSpaceDocumentError, match="must be unique"):
        replace(base, search_parameters=base.search_parameters * 2)


def test_expand_revalidates_corrupted_space_before_calling_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service()
    space = _valid_space()
    object.__setattr__(space, "cardinality", 0)
    called = False

    def unexpected_build(*args: object, **kwargs: object) -> NoOpStrategy:
        nonlocal called
        called = True
        raise AssertionError("factory must not run")

    monkeypatch.setattr(service._registry, "build", unexpected_build)

    with pytest.raises(IncompatibleSearchSpaceDocumentError, match="positive"):
        service.expand(space)
    assert called is False


def test_expand_verifies_checksum_before_calling_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service()
    space = _valid_space()
    object.__setattr__(space, "checksum", "0" * 64)
    called = False

    def unexpected_build(*args: object, **kwargs: object) -> NoOpStrategy:
        nonlocal called
        called = True
        raise AssertionError("factory must not run")

    monkeypatch.setattr(service._registry, "build", unexpected_build)

    with pytest.raises(SearchSpaceChecksumError):
        service.expand(space)
    assert called is False


def test_document_uses_exact_object_schema_and_never_rewrites_a_mutated_version() -> None:
    space = _valid_space()
    document = to_document(space)
    payload = document["search_space"]
    assert isinstance(payload, dict)
    assert payload["schema_version"] == space.schema_version

    object.__setattr__(space, "schema_version", 2)
    with pytest.raises(UnsupportedSearchSpaceSchemaError):
        to_document(space)


def test_version_one_golden_checksums_and_ids_remain_stable() -> None:
    service = _service()
    space = _valid_space()
    combinations = service.expand(space).combinations

    assert space.checksum == "3b12c577ef1d5fe7733e1d2161f18718a215ed4153d8ede82fc4f43dfd29ea41"
    assert space.search_space_id == (
        "252c4b755877fb317cdd6783a30fc678bde947191813891f0ff98a4fcc7b23b0"
    )
    assert [item.combination_id for item in combinations] == [
        "38898d3841e6504be7b74620b4a247320530aa28af74af2e4e65e7280c25bdc7",
        "a12a54e517c139423162f95c05e677b89988479b5ac58784c0f014a7ac0dbd37",
    ]


def test_checksums_and_identifiers_are_stable_and_bound_to_limit_and_values() -> None:
    first = _valid_space(max_combinations=10)
    repeated = _valid_space(max_combinations=10)
    different_limit = _valid_space(max_combinations=11)
    different_value = _service().create(
        "ema-cross-example",
        "1",
        {"fast_period": [2, 4]},
        fixed_parameters={"slow_period": 5, "quantity": Decimal("0.1")},
        max_combinations=10,
    )

    assert first == repeated
    assert first.checksum != different_limit.checksum
    assert first.search_space_id != different_limit.search_space_id
    assert first.checksum != different_value.checksum
    assert first.search_space_id != different_value.search_space_id


def test_combination_documents_checksums_and_ids_are_stable_and_unique() -> None:
    service = _service()
    first = service.expand(_valid_space())
    repeated = service.expand(_valid_space())

    assert first == repeated
    assert len({item.parameters_checksum for item in first.combinations}) == 2
    assert len({item.combination_id for item in first.combinations}) == 2
    assert all(item.parameter_document for item in first.combinations)


@pytest.mark.parametrize("index", ["invalid", True, -1])
def test_combination_constructor_rejects_invalid_index_without_type_error(index: object) -> None:
    combination = _service().expand(_valid_space()).combinations[0]

    with pytest.raises(IncompatibleSearchSpaceDocumentError):
        replace(combination, index=index)  # type: ignore[arg-type]


def test_contracts_are_frozen_and_defensively_copy_input_sequences() -> None:
    values = [2, 3]
    space = _service().create(
        "ema-cross-example",
        "1",
        {"fast_period": values},
        fixed_parameters={"slow_period": 5, "quantity": Decimal("0.1")},
    )
    values.append(4)

    assert space.search_parameters[0].values == (2, 3)
    with pytest.raises(FrozenInstanceError):
        space.cardinality = 99


def test_document_round_trip_returns_fresh_mutable_projection() -> None:
    service = _service()
    space = _valid_space()
    first_document = to_document(space)
    second_document = to_document(space)

    assert service.from_document(first_document) == space
    assert first_document == second_document
    assert first_document is not second_document


def test_incompatible_schema_is_rejected_before_interpretation() -> None:
    document = copy.deepcopy(to_document(_valid_space()))
    payload = document["search_space"]
    assert isinstance(payload, dict)
    payload["schema_version"] = 2

    with pytest.raises(UnsupportedSearchSpaceSchemaError):
        _service().from_document(document)


def test_incorrect_checksum_and_identifier_are_rejected() -> None:
    bad_checksum = copy.deepcopy(to_document(_valid_space()))
    bad_checksum["checksum"] = "0" * 64
    with pytest.raises(SearchSpaceChecksumError):
        _service().from_document(bad_checksum)

    bad_id = copy.deepcopy(to_document(_valid_space()))
    bad_id["search_space_id"] = "0" * 64
    with pytest.raises(IncompatibleSearchSpaceDocumentError, match="identifier"):
        _service().from_document(bad_id)


def test_no_op_plugin_cannot_form_a_space_without_searchable_parameters() -> None:
    with pytest.raises(EmptyParameterSearchSpaceError):
        _service().create("no-op", "1", {})


def test_optimization_package_contains_no_randomness_usage() -> None:
    package = Path(__file__).parents[1] / "app" / "optimization"
    sources = "\n".join(path.read_text(encoding="utf-8") for path in package.glob("*.py"))

    assert "import random" not in sources
    assert "from random" not in sources


@dataclass(frozen=True, slots=True)
class ScalarPlugin:
    descriptor: StrategyPluginDescriptor = StrategyPluginDescriptor(
        "scalar-example",
        "1",
        "Test-only scalar contract.",
        parameters=(
            StrategyParameterSpec("enabled", StrategyParameterKind.BOOLEAN),
            StrategyParameterSpec("label", StrategyParameterKind.STRING),
        ),
    )

    def build(self, parameters: StrategyParameters) -> ScalarStrategy:
        return ScalarStrategy(StrategyDescriptor("scalar-example", "1", parameters))


@dataclass(slots=True)
class ScalarStrategy(NoOpStrategy):
    descriptor: StrategyDescriptor


def test_boolean_and_trimmed_string_values_use_phase3c_normalization() -> None:
    service = ParameterSearchService(
        StrategyPluginRegistry((ScalarPlugin(),)),
        available_indicators=(),
    )

    space = service.create(
        "scalar-example",
        "1",
        {"label": [" beta ", "alpha"]},
        fixed_parameters={"enabled": True},
    )

    assert space.search_parameters[0].values == ("alpha", "beta")
    assert dict(service.expand(space).combinations[0].parameters) == {
        "enabled": True,
        "label": "alpha",
    }


def test_strings_that_normalize_to_same_value_are_rejected() -> None:
    service = ParameterSearchService(
        StrategyPluginRegistry((ScalarPlugin(),)),
        available_indicators=(),
    )
    with pytest.raises(DuplicateSearchParameterValueError):
        service.create(
            "scalar-example",
            "1",
            {"label": ["alpha", " alpha "]},
            fixed_parameters={"enabled": True},
        )


@dataclass(frozen=True, slots=True)
class UnexpectedFactoryPlugin:
    descriptor: StrategyPluginDescriptor = StrategyPluginDescriptor(
        "unexpected",
        "1",
        "Factory with an unexpected programming failure.",
        parameters=(StrategyParameterSpec("enabled", StrategyParameterKind.BOOLEAN),),
    )

    def build(self, parameters: StrategyParameters) -> NoOpStrategy:
        del parameters
        raise RuntimeError("programming failure")


def test_unexpected_factory_programming_errors_are_not_hidden() -> None:
    service = ParameterSearchService(
        StrategyPluginRegistry((UnexpectedFactoryPlugin(),)),
        available_indicators=(),
    )

    with pytest.raises(RuntimeError, match="programming failure"):
        service.create("unexpected", "1", {"enabled": [True]})


def test_factory_semantic_errors_are_wrapped_but_preserve_the_cause() -> None:
    with pytest.raises(InvalidSearchCombinationError) as captured:
        _service().create(
            "ema-cross-example",
            "1",
            {"fast_period": [5]},
            fixed_parameters={"slow_period": 5, "quantity": Decimal("0.1")},
        )

    assert isinstance(captured.value.__cause__, StrategyParameterValidationError)
