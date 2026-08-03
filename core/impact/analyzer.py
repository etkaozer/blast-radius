"""Downstream traversal and collection of everything a change touches.

This is the stage that turns a parsed diff into grounded facts. It is the most
valuable module in the repository and the one with the most DataHub surface
area, which is why it is also the largest remaining piece of OWNER A's work.

Everything it produces is a fact with a provenance: an entity reached by a
lineage path, an owner attached to an entity, a query count over a window. It
does not interpret, rank or narrate. Interpretation is `core.severity`'s job and
narration is `core.agent`'s.
"""

from __future__ import annotations

from contracts.models import ChangeSet, ColumnChange, ColumnImpact, Degradation
from core.config import Settings
from core.datahub.base import DataHubReader
from core.errors import OWNER_A, StubNotImplementedError

_T = "core.impact.analyzer"


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
    raise StubNotImplementedError(
        f"{_T}.analyze_column",
        OWNER_A,
        "ground one ColumnChange in DataHub (lineage, owners, assertions, contracts, usage) "
        "and score it deterministically; returns (ColumnImpact, degradations)",
    )


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
    raise StubNotImplementedError(
        f"{_T}.analyze_change_set",
        OWNER_A,
        "map analyze_column over change_set.column_changes, preserving order, "
        "merging degradations, tolerating per-column failure",
    )
