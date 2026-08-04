"""Turn a `SeverityInput` into a `Severity`, showing all of its work.

The output carries every factor, including the ones that contributed zero, so
that a PR comment can show what was considered and rejected. A reviewer who
disagrees with a score can point at a row rather than at the tool.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Final

from contracts.canonical import sha256_of_json
from contracts.models import FactorName, Severity, SeverityFactor
from core.severity.rules import (
    RULE_VERSION,
    WEIGHTS,
    SeverityInput,
    level_for,
    normalize_change_kind_risk,
    normalize_downstream_reach,
    normalize_flag,
    normalize_hop_proximity,
    normalize_query_usage,
)

#: Contributions and scores are rounded here and nowhere else, so the arithmetic
#: in a report always adds up exactly as displayed.
PRECISION: Final[int] = 2

_CHANGE_KIND_DESCRIPTIONS: Final[dict[str, str]] = {
    "removed": "A removal cannot be absorbed by an alias; every reader must change.",
    "renamed": "A rename breaks every reference that is not updated in the same commit.",
    "type_changed": "A type change breaks readers that cast, compare or aggregate the column.",
    "added": "An added column does not break readers that select columns explicitly.",
}


def _factor(
    name: FactorName,
    raw_value: bool | int | float | str | None,
    normalized: float,
    description: str,
) -> SeverityFactor:
    weight = WEIGHTS[name]
    return SeverityFactor(
        name=name,
        raw_value=raw_value,
        normalized=normalized,
        weight=weight,
        contribution=round(weight * normalized, PRECISION),
        description=description,
    )


def factors_for(severity_input: SeverityInput) -> tuple[SeverityFactor, ...]:
    """Return the seven factors that make up a score, in report order."""
    si = severity_input

    usage_description = (
        (
            "Query usage was UNAVAILABLE in DataHub, not zero. It scored zero because "
            "nothing was measured, not because the column is unused, so the score this "
            f"factor belongs to is a LOWER BOUND: up to {WEIGHTS['query_usage']} further "
            "points could not be assessed."
        )
        if si.query_count is None
        else f"{si.query_count} observed queries in the usage window."
    )
    hop_description = (
        "Nothing downstream was reached."
        if si.nearest_hop_distance is None
        else (
            f"Nearest consumer is {si.nearest_hop_distance} hop(s) away, "
            "so breakage is immediate rather than buffered."
        )
    )

    return (
        _factor(
            "change_kind_risk",
            si.change_kind,
            normalize_change_kind_risk(si.change_kind),
            _CHANGE_KIND_DESCRIPTIONS[si.change_kind],
        ),
        _factor(
            "downstream_reach",
            si.downstream_count,
            normalize_downstream_reach(si.downstream_count),
            f"{si.downstream_count} distinct entities reached through column-level lineage.",
        ),
        _factor(
            "hop_proximity",
            si.nearest_hop_distance,
            normalize_hop_proximity(si.nearest_hop_distance),
            hop_description,
        ),
        _factor(
            "query_usage", si.query_count, normalize_query_usage(si.query_count), usage_description
        ),
        _factor(
            "contract_presence",
            si.has_data_contract,
            normalize_flag(si.has_data_contract),
            "A data contract references the changed column."
            if si.has_data_contract
            else "No data contract references the changed column.",
        ),
        _factor(
            "assertion_presence",
            si.has_assertion,
            normalize_flag(si.has_assertion),
            "An assertion references the changed column."
            if si.has_assertion
            else "No assertion references the changed column.",
        ),
        _factor(
            "critical_consumer",
            si.has_critical_consumer,
            normalize_flag(si.has_critical_consumer),
            "A dashboard, chart or ML entity consumes this column."
            if si.has_critical_consumer
            else "No dashboard, chart or ML entity consumes this column.",
        ),
    )


def inputs_digest(severity_input: SeverityInput) -> str:
    """Return the sha256 that makes a score reproducible.

    Same digest, same rule version, same score. Always.
    """
    return sha256_of_json(asdict(severity_input))


def compute(severity_input: SeverityInput) -> Severity:
    """Score one column change from graph facts alone.

    Pure: no I/O, no clock, no randomness, no network, no model. Called with the
    same input a year from now under `sev-v1`, it returns the same number.
    """
    factors = factors_for(severity_input)
    score = round(sum(f.contribution for f in factors), PRECISION)
    return Severity(
        score=score,
        level=level_for(score),
        rule_version=RULE_VERSION,
        factors=factors,
        inputs_digest=inputs_digest(severity_input),
    )


def overall(severities: tuple[Severity, ...]) -> Severity:
    """Return the severity of a whole pull request: the maximum, not the mean.

    One critical break is a critical pull request. Averaging would let a PR that
    touches nine harmless columns and one load-bearing one look moderate.
    """
    if not severities:
        msg = "overall() requires at least one column severity"
        raise ValueError(msg)
    return max(severities, key=lambda s: s.score)


# ---------------------------------------------------------------------------
# Lower-bound reporting.
#
# The formula is untouched: an unmeasured factor still contributes zero, and a
# score is still the sum of its contributions. What these functions add is the
# ability to SAY so. Without them a reader cannot tell 64.5 ("we looked, and
# there is not much usage") from 64.5 ("we could not look"), and those two
# reports deserve different reactions.
#
# There is no field on `Severity` for this, and `contracts/` is frozen, so the
# statement travels in the two places the schema already allows free text: the
# factor's `description`, written above, and a `Degradation` built by the
# caller from `forgone_points` below.
# ---------------------------------------------------------------------------

#: Factors whose `raw_value is None` means "not measured".
#:
#: Only `query_usage` qualifies, and the distinction is not cosmetic.
#: `usage_count()` returns None exactly when DataHub had no usage aspect, so a
#: None there is a gap. `hop_proximity` is also None-able, but its None means
#: nothing was found downstream — a real answer about the graph, arrived at by
#: looking. Treating the two alike would either hide a gap or invent one.
UNMEASURABLE_FACTORS: Final[frozenset[str]] = frozenset({"query_usage"})


def unmeasured_factors(severity: Severity) -> tuple[SeverityFactor, ...]:
    """Return the factors that scored zero because nothing was measured."""
    return tuple(
        factor
        for factor in severity.factors
        if factor.name in UNMEASURABLE_FACTORS and factor.raw_value is None
    )


def forgone_points(severity: Severity) -> float:
    """Return the maximum points the unmeasured factors could have contributed.

    The honest upper bound on what is missing: every unmeasured factor scored
    zero, and the most any of them could have scored is its full weight.
    """
    return round(sum(factor.weight for factor in unmeasured_factors(severity)), PRECISION)


def is_lower_bound(severity: Severity) -> bool:
    """Return True when at least one factor could not be measured.

    A True here means the real severity is somewhere in
    `[score, score + forgone_points(severity)]`, and that the reported level may
    be an under-statement.
    """
    return bool(unmeasured_factors(severity))


def lower_bound_note(severity: Severity) -> str | None:
    """One sentence for a report degradation, or None when everything was measured."""
    missing = unmeasured_factors(severity)
    if not missing:
        return None
    names = ", ".join(factor.name for factor in missing)
    ceiling = round(severity.score + forgone_points(severity), PRECISION)
    return (
        f"{severity.score} is a LOWER BOUND, not a measurement: {names} could not be "
        f"measured and scored zero. The true severity is between {severity.score} and "
        f"{ceiling} ({level_for(ceiling)}). Read the level as a floor."
    )
