"""Impact analysis: traversal and collection. Deterministic."""

from core.impact.analyzer import analyze_change_set, analyze_column
from core.impact.rules import (
    build_severity_input,
    distinct_downstream,
    has_covering_contract,
    has_critical_consumer,
    nearest_hop,
    usage_count,
)

__all__ = [
    "analyze_change_set",
    "analyze_column",
    "build_severity_input",
    "distinct_downstream",
    "has_covering_contract",
    "has_critical_consumer",
    "nearest_hop",
    "usage_count",
]
