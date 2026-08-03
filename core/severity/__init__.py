"""Deterministic severity scoring.

Import boundary: this package may import `contracts` and the standard library.
It may NOT import `core.agent`, `anthropic`, or anything that reaches a model.
Enforced by `core/tests/test_module_boundaries.py`.
"""

from core.severity.rules import (
    CHANGE_KIND_RISK,
    CRITICAL_ENTITY_TYPES,
    LEVEL_THRESHOLDS,
    RULE_VERSION,
    WEIGHTS,
    SeverityInput,
    is_critical_entity_type,
    level_for,
)
from core.severity.scoring import compute, factors_for, inputs_digest, overall

__all__ = [
    "CHANGE_KIND_RISK",
    "CRITICAL_ENTITY_TYPES",
    "LEVEL_THRESHOLDS",
    "RULE_VERSION",
    "WEIGHTS",
    "SeverityInput",
    "compute",
    "factors_for",
    "inputs_digest",
    "is_critical_entity_type",
    "level_for",
    "overall",
]
