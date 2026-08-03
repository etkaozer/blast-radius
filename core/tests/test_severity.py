"""Unit tests for the deterministic severity engine.

Constraint 3 says severity must be pure deterministic code with unit tests.
These are those tests: properties first, golden fixtures second.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from contracts.loader import iter_fixture_dirs, load_impact_report
from contracts.models import ChangeKind
from core.impact.rules import build_severity_input
from core.severity.rules import (
    LEVEL_THRESHOLDS,
    WEIGHTS,
    SeverityInput,
    level_for,
    normalize_downstream_reach,
    normalize_hop_proximity,
    normalize_query_usage,
)
from core.severity.scoring import compute, overall

BASE = SeverityInput(
    change_kind="removed",
    downstream_count=2,
    nearest_hop_distance=1,
    query_count=100,
    has_data_contract=False,
    has_assertion=False,
    has_critical_consumer=False,
)


def test_weights_sum_to_one_hundred() -> None:
    """A score is readable as a percentage of the worst case only if this holds."""
    assert sum(WEIGHTS.values()) == pytest.approx(100.0)


def test_every_factor_is_reported() -> None:
    result = compute(BASE)
    assert len(result.factors) == len(WEIGHTS)
    assert {f.name for f in result.factors} == set(WEIGHTS)


def test_contributions_sum_to_the_score() -> None:
    """The arithmetic in a PR comment must add up as displayed."""
    result = compute(BASE)
    assert round(sum(f.contribution for f in result.factors), 2) == result.score


def test_computed_by_is_always_deterministic() -> None:
    assert compute(BASE).computed_by == "deterministic"


def test_scoring_is_reproducible() -> None:
    first, second = compute(BASE), compute(BASE)
    assert first.score == second.score
    assert first.inputs_digest == second.inputs_digest


def test_different_inputs_get_different_digests() -> None:
    other = replace(BASE, downstream_count=3)
    assert compute(BASE).inputs_digest != compute(other).inputs_digest


@pytest.mark.parametrize(
    ("kind_a", "kind_b"),
    [("removed", "renamed"), ("renamed", "type_changed"), ("type_changed", "added")],
)
def test_change_kinds_are_ordered_by_risk(kind_a: ChangeKind, kind_b: ChangeKind) -> None:
    a = compute(replace(BASE, change_kind=kind_a))
    b = compute(replace(BASE, change_kind=kind_b))
    assert a.score > b.score


def test_more_downstream_never_lowers_the_score() -> None:
    previous = -1.0
    for count in range(0, 20):
        score = compute(replace(BASE, downstream_count=count)).score
        assert score >= previous
        previous = score


def test_closer_consumers_never_lower_the_score() -> None:
    scores = [compute(replace(BASE, nearest_hop_distance=hop)).score for hop in (1, 2, 3, 4, 5)]
    assert scores == sorted(scores, reverse=True)


def test_more_usage_never_lowers_the_score() -> None:
    scores = [compute(replace(BASE, query_count=q)).score for q in (0, 5, 50, 500, 5000)]
    assert scores == sorted(scores)


def test_contract_and_assertion_and_critical_consumer_raise_the_score() -> None:
    baseline = compute(BASE).score
    assert compute(replace(BASE, has_data_contract=True)).score > baseline
    assert compute(replace(BASE, has_assertion=True)).score > baseline
    assert compute(replace(BASE, has_critical_consumer=True)).score > baseline


def test_unavailable_usage_scores_as_zero_but_is_a_distinct_input() -> None:
    """Missing usage data must never be scored as 'heavily used', and the
    report must still be able to tell it apart from a measured zero."""
    unknown = compute(replace(BASE, query_count=None))
    measured_zero = compute(replace(BASE, query_count=0))
    assert unknown.score == measured_zero.score
    assert unknown.inputs_digest != measured_zero.inputs_digest
    usage_factor = next(f for f in unknown.factors if f.name == "query_usage")
    assert usage_factor.raw_value is None
    assert "unavailable" in (usage_factor.description or "").lower()


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (100.0, "critical"),
        (70.0, "critical"),
        (69.9, "high"),
        (45.0, "high"),
        (44.9, "medium"),
        (20.0, "medium"),
        (19.9, "low"),
        (0.0, "low"),
    ],
)
def test_level_thresholds(score: float, expected: str) -> None:
    assert level_for(score) == expected


def test_level_thresholds_are_descending() -> None:
    values = [t for t, _ in LEVEL_THRESHOLDS]
    assert values == sorted(values, reverse=True)


@pytest.mark.parametrize(
    ("count", "expected"), [(0, 0.0), (1, 0.35), (2, 0.6), (3, 0.6), (4, 0.8), (7, 0.8), (8, 1.0)]
)
def test_downstream_reach_buckets(count: int, expected: float) -> None:
    assert normalize_downstream_reach(count) == expected


@pytest.mark.parametrize(
    ("hop", "expected"), [(None, 0.0), (1, 1.0), (2, 0.6), (3, 0.35), (4, 0.15), (9, 0.15)]
)
def test_hop_proximity_buckets(hop: int | None, expected: float) -> None:
    assert normalize_hop_proximity(hop) == expected


@pytest.mark.parametrize(
    ("queries", "expected"),
    [
        (None, 0.0),
        (0, 0.0),
        (1, 0.25),
        (9, 0.25),
        (10, 0.55),
        (99, 0.55),
        (100, 0.8),
        (999, 0.8),
        (1000, 1.0),
    ],
)
def test_query_usage_buckets(queries: int | None, expected: float) -> None:
    assert normalize_query_usage(queries) == expected


def test_overall_is_the_maximum_not_the_mean() -> None:
    """One critical break is a critical pull request."""
    harmless = compute(SeverityInput("added", 0, None, 0, False, False, False))
    severe = compute(SeverityInput("removed", 9, 1, 5000, True, True, True))
    assert overall((harmless, severe, harmless, harmless)).score == severe.score


def test_overall_rejects_an_empty_report() -> None:
    with pytest.raises(ValueError, match="at least one"):
        overall(())


# ---------------------------------------------------------------------------
# Golden fixtures: the numbers in contracts/fixtures/ are the contract.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("directory", iter_fixture_dirs(), ids=lambda d: d.name)
def test_golden_fixture_scores_are_reproduced(directory: object) -> None:
    report = load_impact_report(directory / "expected_impact_report.json")  # type: ignore[operator]
    for impact in report.column_impacts:
        recomputed = compute(
            build_severity_input(
                impact.change.change_kind,
                impact.downstream,
                impact.query_usage,
                impact.assertions,
                impact.data_contracts,
            )
        )
        assert recomputed.score == impact.severity.score
        assert recomputed.level == impact.severity.level
        assert recomputed.rule_version == impact.severity.rule_version
        assert recomputed.inputs_digest == impact.severity.inputs_digest
        for computed_factor, fixture_factor in zip(
            recomputed.factors, impact.severity.factors, strict=True
        ):
            assert computed_factor.name == fixture_factor.name
            assert computed_factor.normalized == fixture_factor.normalized
            assert computed_factor.contribution == fixture_factor.contribution
