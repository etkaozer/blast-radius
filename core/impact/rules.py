"""Deterministic reduction of graph facts to a severity input.

This is the seam between "what DataHub said" and "what the score is computed
from". It is pure, it is tested, and it contains no I/O, so the interesting part
of the analysis can be exercised without a running DataHub.
"""

from __future__ import annotations

from contracts.models import AssertionRef, ContractRef, DownstreamEntity, QueryUsage
from core.datahub.base import assertion_covers_column, contract_covers_column, coverage_is_unknown
from core.datahub.mapping import sibling_key_of
from core.severity.rules import SeverityInput, is_critical_entity_type


def distinct_downstream(entities: tuple[DownstreamEntity, ...]) -> tuple[DownstreamEntity, ...]:
    """Reduce to one entry per logical entity, keeping the shortest path to each.

    Two different duplications have to collapse here, and only one of them is
    visible as a repeated URN.

    **The same URN twice.** Column-level lineage routinely reaches the same
    dashboard by two routes. Counting it twice inflates `downstream_reach` and,
    through it, the score.

    **The same table under two URNs.** A dbt project emits every model twice —
    once on the `dbt` platform, once on the warehouse's — as DataHub siblings.
    Both are returned, and because the walk crosses the sibling edge, the second
    copy also arrives one hop further out. A live run counted 3 downstream
    entities for 2 logical models, with the hop-2 entry naming the same physical
    table as the hop-1 entry. Deduplicating by URN cannot see this: the URNs
    genuinely differ. `sibling_key_of` collapses them by (name, env).

    The survivor is the nearest one, so the sibling hop never inflates
    `hop_proximity` either. Ties keep the first occurrence, so the result is
    stable for a stable input ordering.

    The cost of the (name, env) key is that two genuinely distinct datasets with
    the same qualified name in the same environment on different platforms — a
    Snowflake table and a Redshift copy, say — collapse into one. That
    understates reach, which is the safe direction for a tool whose whole claim
    is that its score is a floor.
    """
    best: dict[str, DownstreamEntity] = {}
    for entity in entities:
        sibling_key = sibling_key_of(entity.urn)
        key = f"sibling:{sibling_key[0]}:{sibling_key[1]}" if sibling_key else entity.urn
        current = best.get(key)
        if current is None or entity.hop_distance < current.hop_distance:
            best[key] = entity
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
    """Return True when any attached data contract covers the changed column."""
    return any(contract_covers_column(c.state, c.references_changed_column) for c in contracts)


def has_covering_assertion(assertions: tuple[AssertionRef, ...]) -> bool:
    """Return True when any attached assertion references the changed column.

    Deliberately not `len(assertions) > 0`. A dataset-level count awards the
    factor to a change that no assertion actually watches, which is both wrong
    and, since assertions are cheap to add, trivially inflatable.
    """
    return any(assertion_covers_column(a.references_changed_column) for a in assertions)


def unknown_coverage(
    assertions: tuple[AssertionRef, ...],
    contracts: tuple[ContractRef, ...],
) -> tuple[str, ...]:
    """Return the URNs whose column coverage could not be determined.

    These score as "does not cover", because an unmeasured factor must not
    inflate a score. The caller turns a non-empty result into a degradation so
    that the report distinguishes a measured no from an unread one.
    """
    return (
        *(a.urn for a in assertions if coverage_is_unknown(a.references_changed_column)),
        *(c.urn for c in contracts if coverage_is_unknown(c.references_changed_column)),
    )


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
        has_assertion=has_covering_assertion(assertions),
        has_critical_consumer=has_critical_consumer(unique),
    )
