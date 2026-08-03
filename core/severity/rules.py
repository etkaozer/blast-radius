"""The severity rule set. Pure data and pure functions.

This module and `scoring.py` are the only places a severity number is ever
produced. Between them they import `contracts.models` and the standard library,
and nothing else. In particular they cannot reach `core.agent`, which is what
makes "the model never sets severity" a property of the import graph rather than
a promise in a prompt. `core/tests/test_module_boundaries.py` asserts it.

Every constant here is deliberately a step function rather than a curve. A
reviewer needs to be able to re-derive a score by hand from the PR comment, and
"4 downstream entities scored 0.8" is checkable in a way that
"log1p(4)/log1p(20) = 0.537" is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from contracts.models import ChangeKind, FactorName, SeverityLevel

RULE_VERSION: Final[str] = "sev-v1"

#: Weights sum to exactly 100, so a score is directly readable as a percentage
#: of the worst case: a removal with 8+ consumers one hop away, heavy query
#: usage, an active contract, an assertion and a dashboard downstream.
WEIGHTS: Final[dict[FactorName, float]] = {
    "change_kind_risk": 30.0,
    "downstream_reach": 20.0,
    "hop_proximity": 15.0,
    "query_usage": 15.0,
    "contract_presence": 12.0,
    "assertion_presence": 4.0,
    "critical_consumer": 4.0,
}

#: How much each kind of schema change can break a reader that is not updated
#: in the same commit. `added` is not zero because adding a column can still
#: break `select *` consumers and strict contracts, but it is close to zero.
CHANGE_KIND_RISK: Final[dict[ChangeKind, float]] = {
    "removed": 1.0,
    "renamed": 0.85,
    "type_changed": 0.6,
    "added": 0.05,
}

#: Ordered high to low. The first threshold a score meets or exceeds wins.
LEVEL_THRESHOLDS: Final[tuple[tuple[float, SeverityLevel], ...]] = (
    (70.0, "critical"),
    (45.0, "high"),
    (20.0, "medium"),
    (0.0, "low"),
)

#: Entity types where a break is visible to someone outside the data team.
CRITICAL_ENTITY_TYPES: Final[frozenset[str]] = frozenset(
    {"dashboard", "chart", "mlFeature", "mlModel", "mlPrimaryKey"}
)


@dataclass(frozen=True, slots=True)
class SeverityInput:
    """The complete input to the severity engine.

    Every field is a fact read from the DataHub metadata graph or from the
    parsed diff structure. There is deliberately no field that can carry prose:
    not the column description, not the PR body, not the model's opinion. Text
    that wants to influence this score has no parameter to arrive through.

    Every field here affects the score. Nothing here is decorative.
    """

    change_kind: ChangeKind
    downstream_count: int
    nearest_hop_distance: int | None
    query_count: int | None
    has_data_contract: bool
    has_assertion: bool
    has_critical_consumer: bool


def normalize_change_kind_risk(change_kind: ChangeKind) -> float:
    """Return the 0..1 risk weight of a kind of schema change."""
    return CHANGE_KIND_RISK[change_kind]


def normalize_downstream_reach(downstream_count: int) -> float:
    """Return the 0..1 reach score for a number of distinct downstream entities.

    Saturating: the difference between 8 and 80 consumers is not interesting,
    because both mean "you cannot fix this by hand during review".
    """
    if downstream_count <= 0:
        return 0.0
    if downstream_count == 1:
        return 0.35
    if downstream_count <= 3:
        return 0.6
    if downstream_count <= 7:
        return 0.8
    return 1.0


def normalize_hop_proximity(nearest_hop_distance: int | None) -> float:
    """Return the 0..1 proximity score for the closest consumer.

    A direct consumer breaks on the next run. A consumer four hops away is
    usually behind at least one job that will fail first and buy time.
    `None` means nothing downstream was found.
    """
    if nearest_hop_distance is None:
        return 0.0
    if nearest_hop_distance <= 1:
        return 1.0
    if nearest_hop_distance == 2:
        return 0.6
    if nearest_hop_distance == 3:
        return 0.35
    return 0.15


def normalize_query_usage(query_count: int | None) -> float:
    """Return the 0..1 usage score for observed queries in the usage window.

    `None` means DataHub had no usage data, which is NOT the same as zero. It
    scores 0.0 here so the number is never inflated by a guess, and the caller
    is required to emit a `query_usage` degradation so the report never reads
    as "nobody queries this".
    """
    if query_count is None or query_count <= 0:
        return 0.0
    if query_count < 10:
        return 0.25
    if query_count < 100:
        return 0.55
    if query_count < 1000:
        return 0.8
    return 1.0


def normalize_flag(present: bool) -> float:
    """Return 1.0 when a boolean graph fact is present, 0.0 otherwise."""
    return 1.0 if present else 0.0


def level_for(score: float) -> SeverityLevel:
    """Map a 0..100 score onto its level."""
    for threshold, level in LEVEL_THRESHOLDS:
        if score >= threshold:
            return level
    return "low"


def is_critical_entity_type(entity_type: str) -> bool:
    """Return True when a break in this entity type is visible outside the data team."""
    return entity_type in CRITICAL_ENTITY_TYPES
