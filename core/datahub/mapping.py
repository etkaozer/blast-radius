"""Pure translation between DataHub's model and the contract models.

Every function here takes plain data — a URN string, a typed aspect object, a
number — and returns a contract model or a primitive. Nothing here opens a
socket, which is the point: the wire protocol is the part of `core/datahub/`
that cannot be verified without a running DataHub, so as little logic as
possible lives on that side of the line.

`core/tests/test_datahub_mapping.py` exercises all of it against hand-built
aspect objects, so the interesting decisions — which transformation counts as a
rename, when a schemaField URN refers to the column under review, how a missing
usage aspect differs from a measured zero — are tested offline and hold for both
access paths.
"""

from __future__ import annotations

import re
from typing import Final

from contracts.models import (
    AssertionType,
    ContractState,
    EntityType,
    QueryUsage,
    TransformationType,
    UsageSource,
)

#: `urn:li:schemaField:(<datasetUrn>,<fieldPath>)`. The dataset URN itself
#: contains commas and parentheses, so the field path is taken from the LAST
#: comma rather than by splitting.
_SCHEMA_FIELD = re.compile(r"^urn:li:schemaField:\((?P<dataset>.+),(?P<field>[^,]*)\)$")

#: `urn:li:dataset:(urn:li:dataPlatform:<platform>,<name>,<env>)`
_DATASET = re.compile(
    r"^urn:li:dataset:\(urn:li:dataPlatform:(?P<platform>[^,]+),(?P<name>.+),(?P<env>[^,]+)\)$"
)

#: DataHub's `transformOperation` is a free-form string set by whichever
#: ingestion source produced the edge. Anything unrecognised becomes `unknown`
#: rather than being guessed at: the transformation label is shown to a
#: reviewer, and a confidently wrong one is worse than an honest blank.
_TRANSFORMATIONS: Final[dict[str, TransformationType]] = {
    "identity": "identity",
    "copy": "identity",
    "rename": "rename",
    "alias": "rename",
    "cast": "cast",
    "transformation:cast": "cast",
    "expression": "expression",
    "transformation": "expression",
    "aggregation": "aggregation",
    "agg": "aggregation",
    "groupby": "aggregation",
    "join": "join",
    "filter": "filter",
    "where": "filter",
}

#: DataHub entity URNs we can report on, mapped to the report's vocabulary.
_ENTITY_TYPES: Final[dict[str, EntityType]] = {
    "dataset": "dataset",
    "dashboard": "dashboard",
    "chart": "chart",
    "mlFeature": "mlFeature",
    "mlModel": "mlModel",
    "mlPrimaryKey": "mlPrimaryKey",
    "dataJob": "dataJob",
    "dataFlow": "dataFlow",
    "notebook": "notebook",
}

#: `AssertionInfo.type` values, mapped to the report's closed set.
_ASSERTION_TYPES: Final[dict[str, AssertionType]] = {
    "FRESHNESS": "FRESHNESS",
    "VOLUME": "VOLUME",
    "FIELD": "FIELD",
    "SQL": "SQL",
    "DATASET": "DATASET",
    "DATA_SCHEMA": "SCHEMA",
    "SCHEMA": "SCHEMA",
    "CUSTOM": "CUSTOM",
}


def entity_type_of(urn: str) -> EntityType | None:
    """Return the report's entity type for a URN, or None if we do not report it."""
    parts = urn.split(":", maxsplit=3)
    if len(parts) < 4 or parts[0] != "urn" or parts[1] != "li":
        return None
    return _ENTITY_TYPES.get(parts[2])


def dataset_name_of(urn: str) -> str:
    """Return the human-readable name of a dataset URN, or the URN itself."""
    match = _DATASET.match(urn)
    return match.group("name") if match else urn


def platform_of(urn: str) -> str | None:
    """Return the short platform name of a dataset URN ('snowflake'), if any."""
    match = _DATASET.match(urn)
    return match.group("platform") if match else None


def entity_name_of(urn: str) -> str:
    """Return a display name for any entity URN.

    Falls back to the URN, which is ugly but never wrong, and never empty — the
    contract requires a non-empty single-line name.
    """
    if urn.startswith("urn:li:dataset:"):
        return dataset_name_of(urn)
    identifier = urn.rsplit(":", maxsplit=1)[-1].strip("()")
    return identifier or urn


def schema_field_urn(dataset_urn: str, column: str) -> str:
    """Build the URN DataHub uses for one column of one dataset."""
    return f"urn:li:schemaField:({dataset_urn},{column})"


def split_schema_field(urn: str) -> tuple[str, str] | None:
    """Split a schemaField URN into (dataset URN, column), or None if it is not one."""
    match = _SCHEMA_FIELD.match(urn)
    if match is None:
        return None
    return match.group("dataset"), match.group("field")


def column_of(urn: str) -> str | None:
    """Return the column named by a schemaField URN, or None."""
    split = split_schema_field(urn)
    return split[1] if split else None


def transformation_type_of(transform_operation: str | None) -> TransformationType:
    """Map DataHub's free-form `transformOperation` onto the report's closed set."""
    if not transform_operation:
        return "unknown"
    return _TRANSFORMATIONS.get(transform_operation.strip().lower(), "unknown")


def assertion_type_of(raw: str | None) -> AssertionType:
    """Map an `AssertionInfo.type` onto the report's closed set."""
    if not raw:
        return "CUSTOM"
    return _ASSERTION_TYPES.get(str(raw).strip().upper(), "CUSTOM")


def contract_state_of(raw: str | None) -> ContractState:
    """Map a data contract's state onto the report's closed set.

    Anything that is not explicitly PENDING is treated as ACTIVE, because both
    count for `contract_presence` and the failure that matters is dropping a
    contract entirely, not mislabelling which kind it was.
    """
    return "PENDING" if str(raw or "").strip().upper() == "PENDING" else "ACTIVE"


def references_column(candidates: tuple[str | None, ...], dataset_urn: str, column: str) -> bool:
    """Return True when any candidate names `column` of `dataset_urn`.

    Candidates are schemaField URNs and bare field paths, whichever the aspect
    happened to carry. Descriptions are deliberately NOT accepted here: matching
    a column name inside free text would put attacker-controlled prose one step
    away from `contract_presence`, which is a scored factor.
    """
    wanted = schema_field_urn(dataset_urn, column)
    for candidate in candidates:
        if not candidate:
            continue
        if candidate in (wanted, column):
            return True
        split = split_schema_field(candidate)
        if split is not None and split[0] == dataset_urn and split[1] == column:
            return True
    return False


def usage_from(
    window_days: int,
    total_queries: int | None,
    unique_users: int | None = None,
    last_queried_at: str | None = None,
) -> QueryUsage:
    """Build a `QueryUsage`, keeping 'unmeasured' distinct from 'measured zero'.

    `total_queries=None` means DataHub had no usage aspect in the window. It
    becomes `source="unavailable"`, which scores the same as zero and reads
    completely differently in the report. That distinction is the whole reason
    this function exists rather than a constructor call at each call site.
    """
    source: UsageSource = "unavailable" if total_queries is None else "datahub_usage"
    return QueryUsage(
        window_days=window_days,
        query_count=max(0, total_queries or 0),
        source=source,
        distinct_user_count=unique_users,
        last_queried_at=last_queried_at,
    )
