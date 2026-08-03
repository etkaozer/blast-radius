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
    has_covering_contract,
    has_critical_consumer,
    nearest_hop,
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
    """A contract someone is about to depend on still breaks."""
    pending = ContractRef(urn="urn:li:dataContract:x", entity_urn=DATASET, state="PENDING")
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
        (AssertionRef(urn="urn:li:assertion:x", entity_urn=DATASET, assertion_type="FIELD"),),
        (),
    )

    assert result.change_kind == "removed"
    assert result.downstream_count == 2
    assert result.nearest_hop_distance == 1
    assert result.query_count == 42
    assert result.has_assertion is True
    assert result.has_data_contract is False
    assert result.has_critical_consumer is True
