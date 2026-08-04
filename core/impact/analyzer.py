"""Downstream traversal and collection of everything a change touches.

This is the stage that turns a parsed diff into grounded facts. It is the most
valuable module in the repository and the one with the most DataHub surface
area, which is why it is also the largest remaining piece of OWNER A's work.

Everything it produces is a fact with a provenance: an entity reached by a
lineage path, an owner attached to an entity, a query count over a window. It
does not interpret, rank or narrate. Interpretation is `core.severity`'s job and
narration is `core.agent`'s.

## Two kinds of failure, deliberately handled differently

A read that fails against a live DataHub — a timeout, a 500, a permission error
— is an expected operational state. It becomes a `Degradation`, the analysis
continues with the remaining facts, and the report says out loud which capability
was missing and what that costs the reader.

A read that is not implemented is NOT. `StubNotImplementedError` is allowed to
propagate all the way out to the CLI, which halts and names the stub. The
alternative — catching it, defaulting to "no downstream entities, no usage" and
scoring the change 30.0/medium — would be the single most dangerous thing this
repository could do: a report that looks complete, reads as reassuring, and is
made of nothing. See constraint 3 in CLAUDE.md.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import TypeVar

from contracts.models import (
    AssertionRef,
    Capability,
    ChangeSet,
    ColumnChange,
    ColumnChangeRef,
    ColumnImpact,
    ContractRef,
    Degradation,
    DownstreamEntity,
    Owner,
    QueryUsage,
)
from core.config import Settings
from core.datahub.base import DataHubReader
from core.errors import DataHubAccessError
from core.impact.rules import build_severity_input, distinct_downstream, unknown_coverage
from core.severity.scoring import compute, lower_bound_note

_T = "core.impact.analyzer"

_R = TypeVar("_R")

# Typed empty fallbacks. An unannotated `()` infers as `tuple[()]`, which makes
# `_read` resolve its type variable to the empty tuple type and reject the real
# reader's return value.
_NO_DOWNSTREAM: tuple[DownstreamEntity, ...] = ()
_NO_OWNERS: tuple[Owner, ...] = ()
_NO_ASSERTIONS: tuple[AssertionRef, ...] = ()
_NO_CONTRACTS: tuple[ContractRef, ...] = ()


def _read(
    call: Callable[[], _R],
    fallback: _R,
    capability: Capability,
    consequence: str,
    degradations: list[Degradation],
) -> _R:
    """Run one DataHub read, turning an operational failure into a visible degradation.

    Only `DataHubAccessError` is caught. `NotImplementedError` propagates on
    purpose — see the module docstring.
    """
    try:
        return call()
    except DataHubAccessError as exc:
        degradations.append(
            Degradation(
                capability=capability,
                reason=str(exc)[:512],
                consequence=consequence,
            )
        )
        return fallback


def _attributed(
    owners: tuple[Owner, ...],
    source: str,
    for_urn: str,
) -> tuple[Owner, ...]:
    """Stamp provenance onto owners that did not already carry it.

    A reader that already knows an owner was inherited from a container says so,
    and that answer wins: it is strictly more specific than "we asked about this
    URN and got this back".
    """
    return tuple(
        owner.model_copy(
            update={
                "source": owner.source or source,
                "for_urn": owner.for_urn or for_urn,
            }
        )
        for owner in owners
    )


def _deduplicate_owners(owners: tuple[Owner, ...]) -> tuple[Owner, ...]:
    """Keep the first mention of each owner URN.

    Order matters: the changed dataset's owners are collected first, so the
    person who owns the thing being changed is attributed to it rather than to
    whichever downstream dashboard happened to be reached first.
    """
    seen: dict[str, Owner] = {}
    for owner in owners:
        if owner.urn not in seen:
            seen[owner.urn] = owner
    return tuple(seen.values())


def analyze_column(
    change: ColumnChange,
    reader: DataHubReader,
    settings: Settings,
) -> tuple[ColumnImpact, tuple[Degradation, ...]]:
    """Ground one column change in the metadata graph.

    Contract:

    1. `reader.get_lineage(change.dataset_urn, column=change.column,
       direction="DOWNSTREAM", max_hops=settings.max_hops)` for the reached
       entities, each with its full column-level path.
    2. `reader.get_owners` for the changed dataset and for every downstream
       entity; deduplicate by URN and set `source` on each.
    3. `reader.get_assertions` and `reader.get_data_contracts` for the changed
       dataset, marking `references_changed_column`.
    4. `reader.get_dataset_queries(..., window_days=settings.usage_window_days)`.
    5. `core.impact.rules.build_severity_input(...)` then
       `core.severity.compute(...)` — in that order, and BEFORE any untrusted
       text has been read or any prompt has been built.

    Returns the impact with `explanation=None` and `untrusted_findings=()`.
    Those are filled in later by the pipeline, so that this function stays
    free of any model dependency and can be tested against a fake reader.

    Every capability that was unavailable must come back as a `Degradation`
    rather than as a silently missing field. An empty `downstream` tuple means
    "column-level lineage returned nothing", and it must be distinguishable
    from "lineage was not available".
    """
    degradations: list[Degradation] = []

    # -- 1. lineage ----------------------------------------------------------
    reached = _read(
        lambda: reader.get_lineage(
            change.dataset_urn,
            column=change.column,
            direction="DOWNSTREAM",
            max_hops=settings.max_hops,
        ),
        _NO_DOWNSTREAM,
        "column_level_lineage",
        "No downstream entities could be read, so downstream reach and hop proximity "
        "both scored zero. This is NOT evidence that nothing consumes the column.",
        degradations,
    )
    downstream: tuple[DownstreamEntity, ...] = distinct_downstream(reached)

    # An entity reached without a `via_column` was reached at table level. The
    # score still counts it, because it is genuinely downstream, but the report
    # has to say that the fine-grained answer was unavailable — otherwise a
    # catalog with only table-level lineage reads as if it had been analysed
    # column by column.
    if any(entity.via_column is None for entity in downstream):
        degradations.append(
            Degradation(
                capability="column_level_lineage",
                reason=(
                    f"{sum(1 for e in downstream if e.via_column is None)} of {len(downstream)} "
                    "downstream entities were reached without a column-level edge"
                ),
                consequence=(
                    "Those entities are reported as affected, but which of their columns "
                    "carries the change is unknown. Treat their fixes as unverified."
                ),
            )
        )

    # -- 2. owners -----------------------------------------------------------
    collected: list[Owner] = list(
        _attributed(
            _read(
                lambda: reader.get_owners(change.dataset_urn),
                _NO_OWNERS,
                "ownership",
                "Owners of the changed dataset are unknown, so nobody is @-mentioned for it.",
                degradations,
            ),
            "changed_dataset",
            change.dataset_urn,
        )
    )
    for entity in downstream:
        collected.extend(
            _attributed(
                _read(
                    partial(reader.get_owners, entity.urn),
                    _NO_OWNERS,
                    "ownership",
                    "Owners of a downstream entity are unknown; they will not be notified.",
                    degradations,
                ),
                "downstream_entity",
                entity.urn,
            )
        )
    owners = _deduplicate_owners(tuple(collected))

    # -- 3. assertions and data contracts ------------------------------------
    assertions: tuple[AssertionRef, ...] = _read(
        lambda: reader.get_assertions(change.dataset_urn, change.column),
        _NO_ASSERTIONS,
        "assertions",
        "Assertions could not be read, so assertion_presence scored zero. An assertion "
        "naming this column would have raised the score.",
        degradations,
    )
    contracts: tuple[ContractRef, ...] = _read(
        lambda: reader.get_data_contracts(change.dataset_urn, change.column),
        _NO_CONTRACTS,
        "data_contracts",
        "Data contracts could not be read, so contract_presence scored zero. A contract "
        "covering this dataset would have raised the score by up to 12 points.",
        degradations,
    )

    # -- 4. observed usage ---------------------------------------------------
    usage: QueryUsage = _read(
        lambda: reader.get_dataset_queries(
            change.dataset_urn, window_days=settings.usage_window_days
        ),
        QueryUsage(
            window_days=settings.usage_window_days,
            query_count=0,
            source="unavailable",
        ),
        "query_usage",
        "Query usage could not be read. It scored zero, which is NOT the same as "
        "'nobody queries this table'.",
        degradations,
    )
    # -- 5. severity, from graph facts only ----------------------------------
    # Nothing textual has been read at this point, and no prompt exists. The
    # ordering is the security control; see core/pipeline.py.
    severity = compute(
        build_severity_input(
            change.change_kind,
            downstream,
            usage,
            assertions,
            contracts,
        )
    )

    # A factor that could not be measured scored zero, which makes the number
    # an under-statement rather than an estimate. The formula is not adjusted
    # for it — guessing would be worse — so the report says so instead, and
    # says how far the true score could be above what is printed.
    note = lower_bound_note(severity)
    if note is not None:
        degradations.append(
            Degradation(
                capability="query_usage",
                reason="DataHub has no usage statistics for this dataset in the window",
                consequence=note,
            )
        )

    # Coverage that could not be read is a different gap from coverage that is
    # genuinely absent, and only this branch can tell them apart.
    unread = unknown_coverage(assertions, contracts)
    if unread:
        degradations.append(
            Degradation(
                capability="assertions",
                reason=f"column coverage could not be determined for: {', '.join(unread)}"[:512],
                consequence=(
                    "Those assertions or contracts scored as not covering the changed "
                    "column, because an unmeasured factor must not inflate a score. If "
                    "any of them does reference it, the real severity is higher."
                ),
            )
        )

    impact = ColumnImpact(
        change_id=change.id,
        change=ColumnChangeRef(
            dbt_model=change.dbt_model,
            dataset_urn=change.dataset_urn,
            dataset_name=change.dataset_name,
            column=change.column,
            change_kind=change.change_kind,
        ),
        downstream=downstream,
        owners_to_notify=owners,
        assertions=assertions,
        data_contracts=contracts,
        query_usage=usage,
        severity=severity,
        explanation=None,
        untrusted_findings=(),
        fix_ids=(),
    )
    return impact, tuple(degradations)


def _merge_degradations(degradations: tuple[Degradation, ...]) -> tuple[Degradation, ...]:
    """Collapse to one degradation per capability, keeping the first reason.

    Ten columns behind one unreachable DataHub produce ten identical
    degradations, and a report that says the same thing ten times is a report
    nobody finishes reading.
    """
    seen: dict[str, Degradation] = {}
    for degradation in degradations:
        if degradation.capability not in seen:
            seen[degradation.capability] = degradation
    return tuple(seen.values())


def analyze_change_set(
    change_set: ChangeSet,
    reader: DataHubReader,
    settings: Settings,
) -> tuple[tuple[ColumnImpact, ...], tuple[Degradation, ...]]:
    """Ground every column change in a change set.

    Contract: call `analyze_column` per change, preserving input order, and
    merge the degradations, deduplicating by capability. Failing to analyse one
    column must not abandon the others: a partial report with an explicit
    degradation is more useful than no report.
    """
    impacts: list[ColumnImpact] = []
    degradations: list[Degradation] = []
    failed: list[str] = []

    for change in change_set.column_changes:
        try:
            impact, column_degradations = analyze_column(change, reader, settings)
        except DataHubAccessError as exc:
            # One column's analysis collapsed. Say which, say why, keep going.
            failed.append(f"{change.dataset_name}.{change.column} ({change.id}): {exc}")
            continue
        impacts.append(impact)
        degradations.extend(column_degradations)

    if failed:
        degradations.append(
            Degradation(
                capability="column_level_lineage",
                reason=f"{len(failed)} column change(s) could not be analysed: "
                + "; ".join(failed)[:400],
                consequence=(
                    "Those columns are absent from this report entirely. Their absence is "
                    "not a finding of 'no impact' — they were never measured."
                ),
            )
        )

    if not impacts:
        msg = (
            f"none of the {len(change_set.column_changes)} column change(s) could be "
            f"analysed against DataHub; refusing to emit a report with no findings in it"
        )
        raise DataHubAccessError(msg)

    return tuple(impacts), _merge_degradations(tuple(degradations))
