"""A score built on an unmeasured factor must announce itself as a floor.

The README leads with the claim that "no usage data" and "nobody queries this"
are different states and are reported differently. They score identically —
that is deliberate, because guessing at unmeasured usage would be worse — so the
only thing that can carry the difference is what the report SAYS.

Before this, it said nothing: an unavailable `query_usage` scored zero and the
resulting number was printed exactly like a measured one. A reviewer reading
64.5 could not tell whether the tool had looked and found little, or had not
looked. These tests pin the difference.

The formula itself is unchanged, and `test_the_formula_is_untouched` is what
keeps it that way.
"""

from __future__ import annotations

import pytest

from core.severity.rules import WEIGHTS, SeverityInput
from core.severity.scoring import (
    compute,
    forgone_points,
    is_lower_bound,
    lower_bound_note,
    unmeasured_factors,
)

MEASURED = SeverityInput(
    change_kind="removed",
    downstream_count=2,
    nearest_hop_distance=1,
    query_count=0,
    has_data_contract=False,
    has_assertion=False,
    has_critical_consumer=False,
)
UNMEASURED = SeverityInput(
    change_kind="removed",
    downstream_count=2,
    nearest_hop_distance=1,
    query_count=None,
    has_data_contract=False,
    has_assertion=False,
    has_critical_consumer=False,
)


def test_the_formula_is_untouched() -> None:
    """A measured zero and an unmeasured factor still produce the same number.

    This is the constraint the fix had to respect: surface the gap, do not
    silently inflate the score to compensate for it.
    """
    assert compute(MEASURED).score == compute(UNMEASURED).score


def test_an_unmeasured_score_is_flagged_as_a_lower_bound() -> None:
    assert is_lower_bound(compute(UNMEASURED)) is True


def test_a_fully_measured_score_is_not() -> None:
    assert is_lower_bound(compute(MEASURED)) is False
    assert lower_bound_note(compute(MEASURED)) is None


def test_the_unmeasured_factor_is_named() -> None:
    missing = unmeasured_factors(compute(UNMEASURED))
    assert [f.name for f in missing] == ["query_usage"]


def test_the_forgone_points_are_the_factor_weight() -> None:
    """The honest ceiling: an unmeasured factor could have scored its full weight."""
    assert forgone_points(compute(UNMEASURED)) == WEIGHTS["query_usage"]
    assert forgone_points(compute(MEASURED)) == 0.0


def test_the_note_states_the_range_and_the_ceiling_level() -> None:
    severity = compute(UNMEASURED)
    note = lower_bound_note(severity)

    assert note is not None
    assert "LOWER BOUND" in note
    assert str(severity.score) in note
    assert str(round(severity.score + WEIGHTS["query_usage"], 2)) in note


def test_the_ceiling_can_cross_a_level_boundary() -> None:
    """The case that matters: reported `high`, but possibly `critical`.

    62.0 measured, up to 77.0 unmeasured. A reviewer who reads only the level
    would treat this as not-urgent, and the note is the only thing that says
    otherwise.
    """
    severity = compute(
        SeverityInput("removed", 3, 1, None, False, False, True),
    )
    note = lower_bound_note(severity)

    assert severity.level == "high"
    assert note is not None
    assert "critical" in note


def test_the_factor_description_says_unavailable_not_zero() -> None:
    """The description is the only per-factor channel into the report JSON."""
    usage = next(f for f in compute(UNMEASURED).factors if f.name == "query_usage")

    assert usage.raw_value is None
    description = (usage.description or "").lower()
    assert "unavailable" in description
    assert "not zero" in description
    assert "lower bound" in description


def test_a_measured_zero_says_no_such_thing() -> None:
    usage = next(f for f in compute(MEASURED).factors if f.name == "query_usage")

    assert usage.raw_value == 0
    assert "lower bound" not in (usage.description or "").lower()


def test_nothing_downstream_is_not_treated_as_unmeasured() -> None:
    """`hop_proximity` is also None-able, and its None is a real answer.

    Nothing downstream was FOUND, by looking. Reporting that as an unmeasured
    factor would invent a gap and make every leaf table's report hedge.
    """
    isolated = compute(SeverityInput("removed", 0, None, 5, False, False, False))

    hop = next(f for f in isolated.factors if f.name == "hop_proximity")
    assert hop.raw_value is None
    assert is_lower_bound(isolated) is False


@pytest.mark.parametrize("query_count", [0, 1, 100, 5000])
def test_any_measured_usage_produces_a_definite_score(query_count: int) -> None:
    severity = compute(
        SeverityInput("renamed", 1, 2, query_count, False, False, False),
    )
    assert is_lower_bound(severity) is False
    assert forgone_points(severity) == 0.0
