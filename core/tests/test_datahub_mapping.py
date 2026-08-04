"""Tests for the DataHub translation layer.

`core/datahub/mapping.py` is where the parts of DataHub integration that can be
verified without a server live. Everything asserted here holds identically for
the MCP client and the SDK client, which is what "the two access paths must be
behaviourally interchangeable" means in practice.

The URN parsing tests look fussy. They are not: a DataHub dataset URN contains
commas and parentheses inside its own parentheses, so the obvious `split(",")`
is wrong in a way that only shows up on real data, and it is wrong by silently
returning the wrong column name rather than by failing.
"""

from __future__ import annotations

import pytest

from core.datahub.mapping import (
    assertion_type_of,
    column_of,
    contract_state_of,
    dataset_name_of,
    entity_name_of,
    entity_type_of,
    platform_of,
    references_column,
    schema_field_urn,
    split_schema_field,
    transformation_type_of,
    usage_from,
)

DATASET = "urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.dbt_prod.stg_customers,PROD)"


# ---------------------------------------------------------------------------
# URNs
# ---------------------------------------------------------------------------


def test_a_dataset_urn_yields_its_name_and_platform() -> None:
    assert dataset_name_of(DATASET) == "analytics.dbt_prod.stg_customers"
    assert platform_of(DATASET) == "snowflake"


@pytest.mark.parametrize(
    ("urn", "expected"),
    [
        (DATASET, "dataset"),
        ("urn:li:dashboard:(looker,revenue_overview)", "dashboard"),
        ("urn:li:chart:(looker,orders)", "chart"),
        ("urn:li:mlFeature:(customer_features,ltv)", "mlFeature"),
        ("urn:li:dataJob:(urn:li:dataFlow:(airflow,dag,PROD),task)", "dataJob"),
        ("urn:li:corpuser:dana.eng", None),
        ("urn:li:glossaryTerm:pii", None),
        ("not-a-urn", None),
        ("", None),
    ],
)
def test_entity_type_is_read_from_the_urn(urn: str, expected: str | None) -> None:
    assert entity_type_of(urn) == expected


def test_a_schema_field_urn_round_trips() -> None:
    """The dataset URN inside contains commas, so this is not a split on ','."""
    built = schema_field_urn(DATASET, "email")
    assert built == f"urn:li:schemaField:({DATASET},email)"
    assert split_schema_field(built) == (DATASET, "email")
    assert column_of(built) == "email"


def test_a_nested_column_path_survives() -> None:
    urn = schema_field_urn(DATASET, "address.postcode")
    assert column_of(urn) == "address.postcode"


def test_a_non_schema_field_urn_is_not_parsed_as_one() -> None:
    assert split_schema_field(DATASET) is None
    assert column_of(DATASET) is None


def test_an_unparseable_urn_still_yields_a_usable_name() -> None:
    """The contract requires a non-empty single-line name, always."""
    assert entity_name_of("urn:li:dashboard:(looker,revenue)") != ""
    assert entity_name_of("nonsense") == "nonsense"


# ---------------------------------------------------------------------------
# Enum narrowing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("IDENTITY", "identity"),
        ("copy", "identity"),
        ("rename", "rename"),
        ("AS", "unknown"),
        ("CAST", "cast"),
        ("aggregation", "aggregation"),
        ("some_custom_thing", "unknown"),
        (None, "unknown"),
        ("", "unknown"),
    ],
)
def test_transformations_narrow_to_the_closed_set(raw: str | None, expected: str) -> None:
    """An unrecognised label becomes `unknown`, never a plausible guess."""
    assert transformation_type_of(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("FRESHNESS", "FRESHNESS"),
        ("field", "FIELD"),
        ("DATA_SCHEMA", "SCHEMA"),
        ("something_new", "CUSTOM"),
        (None, "CUSTOM"),
    ],
)
def test_assertion_types_narrow_to_the_closed_set(raw: str | None, expected: str) -> None:
    assert assertion_type_of(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("PENDING", "PENDING"), ("pending", "PENDING"), ("ACTIVE", "ACTIVE"), (None, "ACTIVE")],
)
def test_contract_states_narrow_to_the_closed_set(raw: str | None, expected: str) -> None:
    assert contract_state_of(raw) == expected


# ---------------------------------------------------------------------------
# Column reference detection — this one feeds a scored factor.
# ---------------------------------------------------------------------------


def test_a_schema_field_urn_matches_the_column_it_names() -> None:
    assert references_column((schema_field_urn(DATASET, "email"),), DATASET, "email") is True


def test_a_bare_field_path_matches() -> None:
    assert references_column(("email",), DATASET, "email") is True


def test_a_different_column_does_not_match() -> None:
    assert references_column((schema_field_urn(DATASET, "phone"),), DATASET, "email") is False


def test_the_same_column_on_a_different_dataset_does_not_match() -> None:
    other = DATASET.replace("stg_customers", "stg_orders")
    assert references_column((schema_field_urn(other, "email"),), DATASET, "email") is False


def test_prose_that_merely_mentions_the_column_does_not_match() -> None:
    """`references_changed_column` feeds `contract_presence`, which is scored.

    If a sentence containing the column name counted, an attacker who can write
    an assertion description could move a severity score by 12 points.
    """
    prose = "This assertion checks that email is always present and well formed."
    assert references_column((prose,), DATASET, "email") is False


def test_empty_and_missing_candidates_are_ignored() -> None:
    assert references_column((None, "", None), DATASET, "email") is False
    assert references_column((), DATASET, "email") is False


# ---------------------------------------------------------------------------
# Usage — the distinction the whole report hangs on.
# ---------------------------------------------------------------------------


def test_absent_usage_is_unavailable_not_zero() -> None:
    usage = usage_from(30, total_queries=None)
    assert usage.source == "unavailable"
    assert usage.query_count == 0


def test_measured_zero_is_measured() -> None:
    usage = usage_from(30, total_queries=0)
    assert usage.source == "datahub_usage"
    assert usage.query_count == 0


def test_the_two_are_distinguishable() -> None:
    """They score identically. The report must still be able to tell them apart."""
    assert usage_from(30, None).source != usage_from(30, 0).source


def test_usage_carries_its_window_and_extras() -> None:
    usage = usage_from(7, 128, 17, "2026-03-10T22:05:44Z")
    assert usage.window_days == 7
    assert usage.query_count == 128
    assert usage.distinct_user_count == 17
    assert usage.last_queried_at == "2026-03-10T22:05:44Z"


def test_a_negative_count_is_clamped() -> None:
    """The schema forbids a negative count; a bad aspect must not fail the run."""
    assert usage_from(30, -5).query_count == 0
