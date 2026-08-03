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
    def access_path(self) -> Literal["mcp", "sdk"]:
        """Which access path this client uses, for the report's provenance block."""
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

    def get_assertions(self, dataset_urn: str) -> tuple[AssertionRef, ...]:
        """Return assertions attached to a dataset."""
        ...

    def get_data_contracts(self, dataset_urn: str) -> tuple[ContractRef, ...]:
        """Return data contracts attached to a dataset."""
        ...


def contract_covers_column(state: ContractState, references_changed_column: bool | None) -> bool:
    """Return True when a contract should count toward the contract_presence factor.

    A PENDING contract still counts: it represents an agreement someone is about
    to depend on, and breaking it silently is how a contract becomes shelfware.
    """
    return state in ("ACTIVE", "PENDING") or bool(references_changed_column)
