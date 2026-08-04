"""The deterministic reduction from graph facts to a severity input."""

from __future__ import annotations

from contracts.models import (
    AssertionRef,
    ContractRef,
    DownstreamEntity,
    LineageHop,
    QueryUsage,
    Transformation,
)
from core.impact.rules import (
    build_severity_input,
    distinct_downstream,
    has_covering_assertion,
    has_covering_contract,
    has_critical_consumer,
    nearest_hop,
    unknown_coverage,
    usage_count,
)

DATASET = "urn:li:dataset:(urn:li:dataPlatform:snowflake,a.b.c,PROD)"
DASHBOARD = "urn:li:dashboard:(looker,revenue)"


def entity(urn: str, entity_type: str, hop: int) -> DownstreamEntity:
    return DownstreamEntity(
        urn=urn,
        entity_type=entity_type,  # type: ignore[arg-type]
        name=urn.split(",")[-2] if "," in urn else urn,
        hop_distance=hop,
        path=(
            LineageHop(
                from_urn=DATASET,
                to_urn=urn,
                transformation=Transformation(type="identity"),
            ),
        ),
    )


def test_duplicate_urns_are_counted_once() -> None:
    """Two lineage routes to one dashboard is one dashboard."""
    entities = (entity(DASHBOARD, "dashboard", 3), entity(DASHBOARD, "dashboard", 2))
    assert len(distinct_downstream(entities)) == 1


def test_deduplication_keeps_the_shortest_path() -> None:
    entities = (entity(DASHBOARD, "dashboard", 3), entity(DASHBOARD, "dashboard", 1))
    assert distinct_downstream(entities)[0].hop_distance == 1


def test_nearest_hop_of_nothing_is_none() -> None:
    assert nearest_hop(()) is None


def test_nearest_hop_picks_the_minimum() -> None:
    entities = (entity(DASHBOARD, "dashboard", 3), entity(DATASET, "dataset", 2))
    assert nearest_hop(entities) == 2


def test_dashboards_and_ml_entities_are_critical_consumers() -> None:
    assert has_critical_consumer((entity(DASHBOARD, "dashboard", 1),))
    assert has_critical_consumer((entity("urn:li:mlFeature:(a,b)", "mlFeature", 1),))
    assert not has_critical_consumer((entity(DATASET, "dataset", 1),))


def test_pending_contracts_count() -> None:
    """A contract someone is about to depend on still breaks.

    Updated for the column-level coverage rule: PENDING still counts, but only
    once the contract actually references the changed column. Before that fix
    this test passed with no `references_changed_column` at all, which is what
    made the old behaviour look correct.
    """
    pending = ContractRef(
        urn="urn:li:dataContract:x",
        entity_urn=DATASET,
        state="PENDING",
        references_changed_column=True,
    )
    assert has_covering_contract((pending,))


def test_unavailable_usage_is_none_not_zero() -> None:
    unavailable = QueryUsage(window_days=30, query_count=0, source="unavailable")
    measured = QueryUsage(window_days=30, query_count=0, source="datahub_usage")
    assert usage_count(unavailable) is None
    assert usage_count(measured) == 0


def test_build_severity_input_reduces_the_graph_correctly() -> None:
    downstream = (
        entity(DATASET, "dataset", 1),
        entity(DASHBOARD, "dashboard", 2),
        entity(DASHBOARD, "dashboard", 3),  # duplicate, longer route
    )
    result = build_severity_input(
        "removed",
        downstream,
        QueryUsage(window_days=30, query_count=42, source="datahub_usage"),
        (
            AssertionRef(
                urn="urn:li:assertion:x",
                entity_urn=DATASET,
                assertion_type="FIELD",
                references_changed_column=True,
            ),
        ),
        (),
    )

    assert result.change_kind == "removed"
    assert result.downstream_count == 2
    assert result.nearest_hop_distance == 1
    assert result.query_count == 42
    assert result.has_assertion is True
    assert result.has_data_contract is False
    assert result.has_critical_consumer is True


# ---------------------------------------------------------------------------
# Regression: column-level coverage.
#
# Both factors used to be dataset-level. `contract_covers_column` was
# `state in ("ACTIVE","PENDING") or bool(references_changed_column)`, and since
# ContractState has exactly those two members the right-hand side was dead code
# — every attached contract scored the full 12 points regardless of which
# column it governed. `has_assertion` was `len(assertions) > 0`, with the same
# consequence for 4 points. Both are cheap to attach, so both were inflatable.
# ---------------------------------------------------------------------------


def contract(state: str = "ACTIVE", references: bool | None = None) -> ContractRef:
    return ContractRef(
        urn="urn:li:dataContract:x",
        entity_urn=DATASET,
        state=state,  # type: ignore[arg-type]
        references_changed_column=references,
    )


def assertion(references: bool | None = None) -> AssertionRef:
    return AssertionRef(
        urn="urn:li:assertion:x",
        entity_urn=DATASET,
        assertion_type="FIELD",
        references_changed_column=references,
    )


def test_a_contract_on_another_column_does_not_count() -> None:
    """The tautology, stated as a test: this used to score the full 12 points."""
    assert has_covering_contract((contract(references=False),)) is False


def test_a_contract_on_the_changed_column_counts() -> None:
    assert has_covering_contract((contract(references=True),)) is True


def test_a_contract_of_unknown_coverage_does_not_count() -> None:
    """Unmeasured must never inflate. It is reported as a degradation instead."""
    assert has_covering_contract((contract(references=None),)) is False


def test_one_covering_contract_among_several_is_enough() -> None:
    contracts = (contract(references=False), contract(references=True))
    assert has_covering_contract(contracts) is True


def test_contract_state_is_still_honoured() -> None:
    assert has_covering_contract((contract("PENDING", references=True),)) is True
    assert has_covering_contract((contract("ACTIVE", references=True),)) is True


def test_an_assertion_on_another_column_does_not_count() -> None:
    """This used to be `len(assertions) > 0` and scored 4 points."""
    assert has_covering_assertion((assertion(references=False),)) is False


def test_an_assertion_on_the_changed_column_counts() -> None:
    assert has_covering_assertion((assertion(references=True),)) is True


def test_an_assertion_of_unknown_coverage_does_not_count() -> None:
    assert has_covering_assertion((assertion(references=None),)) is False


def test_no_assertions_at_all_does_not_count() -> None:
    assert has_covering_assertion(()) is False


def test_unknown_coverage_is_distinguishable_from_absent_coverage() -> None:
    """Both score zero. Only one of them is a gap the reviewer should see."""
    measured_no = unknown_coverage((assertion(references=False),), (contract(references=False),))
    unread = unknown_coverage((assertion(references=None),), (contract(references=None),))

    assert measured_no == ()
    assert len(unread) == 2


def test_the_severity_input_reflects_column_level_coverage() -> None:
    """End to end through the reduction, both factors at once."""
    irrelevant = build_severity_input(
        "removed",
        (),
        QueryUsage(window_days=30, query_count=1, source="datahub_usage"),
        (assertion(references=False),),
        (contract(references=False),),
    )
    relevant = build_severity_input(
        "removed",
        (),
        QueryUsage(window_days=30, query_count=1, source="datahub_usage"),
        (assertion(references=True),),
        (contract(references=True),),
    )

    assert irrelevant.has_assertion is False
    assert irrelevant.has_data_contract is False
    assert relevant.has_assertion is True
    assert relevant.has_data_contract is True
