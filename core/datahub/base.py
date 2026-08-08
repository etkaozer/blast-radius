"""The single read interface over DataHub, and the types it returns.

blast-radius supports two access paths and must behave identically on both:

* **MCP** — `mcp-server-datahub`, the path a DataHub agent would use, and the
  one the hackathon is about;
* **Python SDK** — `acryl-datahub` / `datahub-agent-context`, the path that
  works in a CI container with no MCP runtime, and the fallback when a
  mutation tool is unavailable.

Everything above this module is written against `DataHubReader` and cannot tell
which path served a call. `ImpactReport.datahub.access_path` records which one
did, so a surprising report can be traced to a surprising client.

Return types are the contract models wherever one already exists. Where they do
not, the local dataclasses below stay deliberately small: this is a read
interface, not a mirror of DataHub's model.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from contracts.models import (
    AccessPath,
    AssertionRef,
    ContractRef,
    ContractState,
    DownstreamEntity,
    EntityType,
    LineageHop,
    Owner,
    QueryUsage,
)
from core.untrusted.envelope import UntrustedEnvelope

LineageDirection = Literal["DOWNSTREAM", "UPSTREAM"]

#: How far column-level lineage is walked by default. Three hops covers
#: staging -> mart -> consumer, which is where the interesting breakage lives.
DEFAULT_MAX_HOPS: int = 3

#: Default query-usage window, matching DataHub's own default aggregation.
DEFAULT_USAGE_WINDOW_DAYS: int = 30


@dataclass(frozen=True, slots=True)
class EntityRef:
    """A DataHub entity as returned by search or a bulk get."""

    urn: str
    entity_type: EntityType
    name: str
    platform: str | None = None
    url: str | None = None


@dataclass(frozen=True, slots=True)
class SchemaFieldInfo:
    """One column of a dataset's schema.

    `description` is an `UntrustedEnvelope` rather than a `str` on purpose.
    Field documentation read back from DataHub is written by the same people who
    write dbt descriptions, and it reaches the same prompts. Typing it as
    untrusted at the boundary means a caller cannot accidentally concatenate it
    into a prompt without going through `core.untrusted`.
    """

    field_path: str
    native_type: str | None = None
    nullable: bool | None = None
    is_primary_key: bool | None = None
    description: UntrustedEnvelope | None = None


@dataclass(frozen=True, slots=True)
class LineagePath:
    """One column-level path between two entities."""

    source_urn: str
    target_urn: str
    hops: tuple[LineageHop, ...]

    @property
    def hop_distance(self) -> int:
        """Number of lineage edges on this path."""
        return len(self.hops)


@runtime_checkable
class DataHubReader(Protocol):
    """Every read blast-radius performs against DataHub.

    Implementations must be side-effect free. Nothing in this protocol writes;
    mutations live in `core.writeback` behind a separate interface so that a
    read-only token is enough to run an analysis.
    """

    @property
    def access_path(self) -> AccessPath:
        """Which access path this client uses, for the report's provenance block.

        `AccessPath` rather than a local literal because a composed reader
        reports `"mcp+sdk"`, and the report's provenance must be the same closed
        set the schema publishes.
        """
        ...

    def search(
        self,
        query: str,
        entity_types: Sequence[EntityType] | None = None,
        limit: int = 25,
    ) -> tuple[EntityRef, ...]:
        """Full-text search across the catalog."""
        ...

    def get_entities(self, urns: Sequence[str]) -> tuple[EntityRef, ...]:
        """Resolve URNs to entities in one round trip, preserving input order."""
        ...

    def list_schema_fields(self, dataset_urn: str) -> tuple[SchemaFieldInfo, ...]:
        """Return the dataset's columns, with descriptions typed as untrusted."""
        ...

    def get_lineage(
        self,
        urn: str,
        column: str | None = None,
        direction: LineageDirection = "DOWNSTREAM",
        max_hops: int = DEFAULT_MAX_HOPS,
    ) -> tuple[DownstreamEntity, ...]:
        """Walk column-level lineage from `urn`.`column` and return what it reaches."""
        ...

    def get_lineage_paths_between(
        self,
        source_urn: str,
        target_urn: str,
        source_column: str | None = None,
        max_hops: int = DEFAULT_MAX_HOPS,
    ) -> tuple[LineagePath, ...]:
        """Return every column-level path between two entities."""
        ...

    def get_dataset_queries(
        self,
        dataset_urn: str,
        window_days: int = DEFAULT_USAGE_WINDOW_DAYS,
    ) -> QueryUsage:
        """Return observed query usage for a dataset."""
        ...

    def get_owners(self, urn: str) -> tuple[Owner, ...]:
        """Return the owners of an entity, including container-inherited ones."""
        ...

    def get_assertions(
        self, dataset_urn: str, column: str | None = None
    ) -> tuple[AssertionRef, ...]:
        """Return assertions attached to a dataset.

        `column` is the column under review. Without it an implementation cannot
        populate `references_changed_column`, which is the field that separates
        "this assertion will definitely fail" from "this dataset has assertions".
        """
        ...

    def get_data_contracts(
        self, dataset_urn: str, column: str | None = None
    ) -> tuple[ContractRef, ...]:
        """Return data contracts attached to a dataset, ACTIVE and PENDING."""
        ...


def contract_covers_column(state: ContractState, references_changed_column: bool | None) -> bool:
    """Return True when a contract should count toward the contract_presence factor.

    Coverage means the contract references the column under review. A contract
    that governs a different column of the same dataset is not evidence that
    THIS change breaks an agreement, and counting it awarded the full 12-point
    factor to any dataset that happened to have a contract attached anywhere.

    A PENDING contract still counts once it covers the column: it represents an
    agreement someone is about to depend on, and breaking it silently is how a
    contract becomes shelfware. The state test is retained rather than dropped
    because it states the intent, and because widening `ContractState` later
    would otherwise silently start counting states nobody has considered.

    `None` means the reader could not determine coverage. It does NOT count —
    an unmeasured factor must never inflate a score — and obliges the caller to
    emit a `data_contracts` degradation so the gap is visible in the report.
    """
    return state in ("ACTIVE", "PENDING") and references_changed_column is True


def assertion_covers_column(references_changed_column: bool | None) -> bool:
    """Return True when an assertion should count toward the assertion_presence factor.

    Same rule as `contract_covers_column`, and for the same reason: an assertion
    on an unrelated column of the same dataset says nothing about whether this
    change breaks anything. `None` is unmeasured, does not count, and obliges
    the caller to degrade.
    """
    return references_changed_column is True


def coverage_is_unknown(references_changed_column: bool | None) -> bool:
    """Return True when coverage could not be determined, as opposed to being absent.

    The caller uses this to tell "there is an assertion and it does not name the
    column" (a measured no) from "there is an assertion and we could not read
    which column it names" (a gap), which score identically and read nothing
    alike.
    """
    return references_changed_column is None
