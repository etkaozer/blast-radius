"""Deterministic reduction of graph facts to a severity input.

This is the seam between "what DataHub said" and "what the score is computed
from". It is pure, it is tested, and it contains no I/O, so the interesting part
of the analysis can be exercised without a running DataHub.
"""

from __future__ import annotations

from contracts.models import AssertionRef, ContractRef, DownstreamEntity, QueryUsage
from core.datahub.base import contract_covers_column
from core.severity.rules import SeverityInput, is_critical_entity_type


def distinct_downstream(entities: tuple[DownstreamEntity, ...]) -> tuple[DownstreamEntity, ...]:
    """Deduplicate by URN, keeping the shortest path to each entity.

    Column-level lineage routinely reaches the same dashboard by two routes.
    Counting it twice would inflate `downstream_reach` and, through it, the
    severity score. Keeping the shortest path also keeps `hop_proximity`
    honest.

    Ties on hop distance keep the first occurrence, so the result is stable for
    a stable input ordering.
    """
    best: dict[str, DownstreamEntity] = {}
    for entity in entities:
        current = best.get(entity.urn)
        if current is None or entity.hop_distance < current.hop_distance:
            best[entity.urn] = entity
    return tuple(best.values())


def nearest_hop(entities: tuple[DownstreamEntity, ...]) -> int | None:
    """Return the smallest hop distance, or None when nothing is downstream."""
    if not entities:
        return None
    return min(e.hop_distance for e in entities)


def has_critical_consumer(entities: tuple[DownstreamEntity, ...]) -> bool:
    """Return True when any consumer is visible outside the data team."""
    return any(is_critical_entity_type(e.entity_type) for e in entities)


def has_covering_contract(contracts: tuple[ContractRef, ...]) -> bool:
    """Return True when any attached data contract counts for scoring."""
    return any(contract_covers_column(c.state, c.references_changed_column) for c in contracts)


def usage_count(usage: QueryUsage) -> int | None:
    """Return the observed query count, or None when usage was never measured.

    The distinction matters: `None` scores zero AND obliges the caller to emit a
    `query_usage` degradation, so a report never implies a table is unused when
    the truth is that nobody ingested usage.
    """
    if usage.source == "unavailable":
        return None
    return usage.query_count


def build_severity_input(
    change_kind: str,
    downstream: tuple[DownstreamEntity, ...],
    usage: QueryUsage,
    assertions: tuple[AssertionRef, ...],
    contracts: tuple[ContractRef, ...],
) -> SeverityInput:
    """Reduce one column's graph facts to the severity engine's input.

    Note what is not a parameter: the column description, the PR body, the model
    output. There is nowhere for prose to enter, which is the whole point.
    """
    unique = distinct_downstream(downstream)
    return SeverityInput(
        change_kind=change_kind,  # type: ignore[arg-type]
        downstream_count=len(unique),
        nearest_hop_distance=nearest_hop(unique),
        query_count=usage_count(usage),
        has_data_contract=has_covering_contract(contracts),
        has_assertion=len(assertions) > 0,
        has_critical_consumer=has_critical_consumer(unique),
    )
