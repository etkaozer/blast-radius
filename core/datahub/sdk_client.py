"""DataHub access through the Python SDK (`acryl-datahub`).

The second of the two supported access paths. It exists for three reasons:

1. a CI container that cannot run an MCP subprocess still needs to work;
2. some reads are cheaper as a single GraphQL query than as a tool call loop;
3. when a mutation tool is missing, `core.writeback` falls back to the SDK, and
   a fallback whose read path was never exercised is not a fallback.

The two clients must be behaviourally interchangeable. Any difference a caller
can observe is a bug in one of them, not a feature of either. OWNER A should run
the same test suite against both.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from contracts.models import (
    AssertionRef,
    ContractRef,
    DownstreamEntity,
    EntityType,
    Owner,
    QueryUsage,
)
from core.datahub.base import (
    DEFAULT_MAX_HOPS,
    DEFAULT_USAGE_WINDOW_DAYS,
    EntityRef,
    LineageDirection,
    LineagePath,
    SchemaFieldInfo,
)
from core.errors import OWNER_A, StubNotImplementedError

_T = "core.datahub.sdk_client.SdkDataHubReader"


class SdkDataHubReader:
    """`DataHubReader` backed by acryl-datahub. Satisfies the protocol structurally."""

    def __init__(self, gms_url: str, token: str | None = None) -> None:
        self._gms_url = gms_url
        self._token = token

    @property
    def access_path(self) -> Literal["mcp", "sdk"]:
        """Report provenance: this client is the SDK path."""
        return "sdk"

    def search(
        self,
        query: str,
        entity_types: Sequence[EntityType] | None = None,
        limit: int = 25,
    ) -> tuple[EntityRef, ...]:
        """Full-text search over the catalog.

        Contract: GraphQL `searchAcrossEntities`. Same result shape as the MCP
        client for the same query, including ordering.
        """
        raise StubNotImplementedError(
            f"{_T}.search", OWNER_A, "GraphQL searchAcrossEntities -> tuple[EntityRef, ...]"
        )

    def get_entities(self, urns: Sequence[str]) -> tuple[EntityRef, ...]:
        """Resolve URNs to entities in one round trip, preserving input order.

        Contract: one batched GraphQL call, not one call per URN.
        """
        raise StubNotImplementedError(
            f"{_T}.get_entities", OWNER_A, "batched GraphQL entity fetch, order preserving"
        )

    def list_schema_fields(self, dataset_urn: str) -> tuple[SchemaFieldInfo, ...]:
        """Return the dataset's columns, descriptions wrapped as untrusted.

        Contract: read the `schemaMetadata` aspect. Field descriptions MUST be
        wrapped with `core.untrusted.envelope` before leaving this method.
        """
        raise StubNotImplementedError(
            f"{_T}.list_schema_fields",
            OWNER_A,
            "schemaMetadata aspect; descriptions wrapped as UntrustedEnvelope",
        )

    def get_lineage(
        self,
        urn: str,
        column: str | None = None,
        direction: LineageDirection = "DOWNSTREAM",
        max_hops: int = DEFAULT_MAX_HOPS,
    ) -> tuple[DownstreamEntity, ...]:
        """Walk COLUMN-LEVEL lineage and return the entities reached.

        Contract: identical to `McpDataHubReader.get_lineage`, including the
        rule that table-level-only reachability is never reported as
        column-level. Read `upstreamLineage.fineGrainedLineages` and traverse
        breadth-first so that the first time an entity is seen is at its
        shortest hop distance.
        """
        raise StubNotImplementedError(
            f"{_T}.get_lineage",
            OWNER_A,
            "fineGrainedLineages BFS to max_hops, dedup by URN keeping shortest path",
        )

    def get_lineage_paths_between(
        self,
        source_urn: str,
        target_urn: str,
        source_column: str | None = None,
        max_hops: int = DEFAULT_MAX_HOPS,
    ) -> tuple[LineagePath, ...]:
        """Return every column-level path between two entities, shortest first."""
        raise StubNotImplementedError(
            f"{_T}.get_lineage_paths_between",
            OWNER_A,
            "column-level path enumeration between two URNs, shortest first",
        )

    def get_dataset_queries(
        self,
        dataset_urn: str,
        window_days: int = DEFAULT_USAGE_WINDOW_DAYS,
    ) -> QueryUsage:
        """Return observed query usage for a dataset.

        Contract: read the `datasetUsageStatistics` aspect over `window_days`.
        Return `source="unavailable"` when the aspect is absent.
        """
        raise StubNotImplementedError(
            f"{_T}.get_dataset_queries",
            OWNER_A,
            "datasetUsageStatistics over the window; 'unavailable' is distinct from zero",
        )

    def get_owners(self, urn: str) -> tuple[Owner, ...]:
        """Return owners of an entity, including container-inherited ones.

        Contract: read the `ownership` aspect, then the container's, marking
        inherited owners with `source="container_inherited"`.
        """
        raise StubNotImplementedError(
            f"{_T}.get_owners", OWNER_A, "ownership aspect plus container inheritance"
        )

    def get_assertions(self, dataset_urn: str) -> tuple[AssertionRef, ...]:
        """Return assertions attached to a dataset, with column reference detection."""
        raise StubNotImplementedError(
            f"{_T}.get_assertions", OWNER_A, "assertions aspect with column reference detection"
        )

    def get_data_contracts(self, dataset_urn: str) -> tuple[ContractRef, ...]:
        """Return data contracts attached to a dataset, ACTIVE and PENDING."""
        raise StubNotImplementedError(
            f"{_T}.get_data_contracts", OWNER_A, "dataContract aspect, ACTIVE and PENDING"
        )
