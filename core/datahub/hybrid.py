"""One reader composed from both access paths.

`mcp-server-datahub` cannot answer two of the nine reads in `DataHubReader` on
an open-source DataHub — it has no data-contract tool at all, and its assertion
tool is DataHub Cloud only (see `core/datahub/mcp_client.py` for the evidence).
Those two reads feed `contract_presence` (12 points) and `assertion_presence`
(4 points), so a pure-MCP run on an open-source catalog cannot produce two of
the seven severity factors.

The alternatives were: report a lower score with two degradations, which makes
the tool wrong in the direction that gets someone paged; or silently return
empty, which is the same thing without the disclosure. This module takes the
third option — serve those reads from the SDK and say so.

`access_path` is `"mcp+sdk"` and not `"mcp"`. That value was added to
`contracts/impact_report.schema.json` for this purpose, and it is why the impact
report's `schema_version` is 1.1.0. A run served partly by the SDK that reported
itself as an MCP run would misattribute two factors, and provenance that lies
under composition is worse than no provenance.

## The split

| Served by MCP | Served by the SDK |
| --- | --- |
| `search`, `get_entities`, `list_schema_fields` | `get_assertions` — Cloud-only tool |
| `get_lineage`, `get_lineage_paths_between` | `get_data_contracts` — no tool exists |
| `get_owners` (derived from `get_entities`) | `get_dataset_queries` — see below |

`get_dataset_queries` is delegated even though MCP has a tool for it, because
the tool reads catalogued Query entities while the `query_usage` factor is
defined on the `datasetUsageStatistics` aspect. Those are different
measurements, and scoring one as if it were the other would understate usage on
exactly the catalogs that ingest usage properly.

Lineage — the heart of the analysis, and the read the hackathon is about — is
served by MCP.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal

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
    ReaderNote,
    SchemaFieldInfo,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from core.datahub.mcp_client import McpDataHubReader
    from core.datahub.sdk_client import SdkDataHubReader

#: The reads this composition routes to the SDK, as a stable, inspectable fact
#: rather than something a reader has to infer from the code below. `doctor` and
#: the report both name them, so a reviewer can see which path answered what.
SDK_SERVED_READS: tuple[str, ...] = (
    "get_assertions",
    "get_data_contracts",
    "get_dataset_queries",
)

SPLIT_EXPLANATION = (
    "Lineage, schema, entities and ownership were read over MCP. Assertions, data "
    "contracts and query usage were read with the Python SDK: mcp-server-datahub "
    "exposes no data-contract tool, its assertion tool is DataHub Cloud only, and "
    "its query tool reads catalogued queries rather than usage statistics."
)


class HybridDataHubReader:
    """`DataHubReader` that serves each read from the path that can answer it."""

    def __init__(self, mcp: McpDataHubReader, sdk: SdkDataHubReader) -> None:
        self._mcp = mcp
        self._sdk = sdk

    @property
    def access_path(self) -> Literal["mcp", "sdk", "mcp+sdk"]:
        """Report provenance: composed, and never one of its halves."""
        return "mcp+sdk"

    def close(self) -> None:
        """Shut the MCP server subprocess down. The SDK holds no subprocess."""
        self._mcp.close()

    def drain_notes(self) -> tuple[ReaderNote, ...]:
        """Forward the MCP half's notes. Lineage is served there, and it is the
        only read that has anything to drop.

        Without this the composition would swallow them, and a
        `HybridDataHubReader` would under-report exactly where a bare
        `McpDataHubReader` would not — the drift rule 4 in `core/CLAUDE.md`
        forbids.
        """
        return self._mcp.drain_notes()

    # -- served by MCP -------------------------------------------------------

    def search(
        self,
        query: str,
        entity_types: Sequence[EntityType] | None = None,
        limit: int = 25,
    ) -> tuple[EntityRef, ...]:
        """Full-text search across the catalog, over MCP."""
        return self._mcp.search(query, entity_types, limit)

    def get_entities(self, urns: Sequence[str]) -> tuple[EntityRef, ...]:
        """Resolve URNs to entities, over MCP."""
        return self._mcp.get_entities(urns)

    def list_schema_fields(self, dataset_urn: str) -> tuple[SchemaFieldInfo, ...]:
        """Return the dataset's columns, over MCP."""
        return self._mcp.list_schema_fields(dataset_urn)

    def get_lineage(
        self,
        urn: str,
        column: str | None = None,
        direction: LineageDirection = "DOWNSTREAM",
        max_hops: int = DEFAULT_MAX_HOPS,
    ) -> tuple[DownstreamEntity, ...]:
        """Walk column-level lineage, over MCP. The read this composition exists to keep."""
        return self._mcp.get_lineage(urn, column, direction, max_hops)

    def get_lineage_paths_between(
        self,
        source_urn: str,
        target_urn: str,
        source_column: str | None = None,
        max_hops: int = DEFAULT_MAX_HOPS,
    ) -> tuple[LineagePath, ...]:
        """Return every column-level path between two entities, over MCP."""
        return self._mcp.get_lineage_paths_between(source_urn, target_urn, source_column, max_hops)

    def get_owners(self, urn: str) -> tuple[Owner, ...]:
        """Return owners, over MCP, derived from the entity's own ownership aspect."""
        return self._mcp.get_owners(urn)

    # -- served by the SDK ---------------------------------------------------

    def get_dataset_queries(
        self,
        dataset_urn: str,
        window_days: int = DEFAULT_USAGE_WINDOW_DAYS,
    ) -> QueryUsage:
        """Return observed query usage from `datasetUsageStatistics`, over the SDK."""
        return self._sdk.get_dataset_queries(dataset_urn, window_days)

    def get_assertions(
        self, dataset_urn: str, column: str | None = None
    ) -> tuple[AssertionRef, ...]:
        """Return assertions, over the SDK: MCP's assertion tool is Cloud only."""
        return self._sdk.get_assertions(dataset_urn, column)

    def get_data_contracts(
        self, dataset_urn: str, column: str | None = None
    ) -> tuple[ContractRef, ...]:
        """Return data contracts, over the SDK: MCP has no data-contract tool."""
        return self._sdk.get_data_contracts(dataset_urn, column)
