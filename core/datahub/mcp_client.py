"""DataHub access through `mcp-server-datahub`.

This is the path the hackathon is about: the same tools a DataHub agent would
call, driven from a CI job. It is the default (`BLAST_RADIUS_DATAHUB_MODE=mcp`).

Implementation notes for OWNER A:

* The MCP server is a subprocess speaking MCP over stdio. Connect once per run
  and reuse the session; a new connection per lineage hop turns a 3-second
  analysis into a 30-second one.
* Verify the exact tool names and argument shapes against the installed server
  version before writing the calls — do not trust the names in these docstrings,
  which are what we expect rather than what we have confirmed.
* Every free-text field that comes back (descriptions, documentation, glossary
  terms) must be wrapped with `core.untrusted.envelope` at this boundary, not
  later. Once a raw `str` escapes this module, nothing downstream can tell it
  apart from a trusted one.
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

_T = "core.datahub.mcp_client.McpDataHubReader"


class McpDataHubReader:
    """`DataHubReader` backed by mcp-server-datahub. Satisfies the protocol structurally."""

    def __init__(self, server_command: str, gms_url: str, token: str | None = None) -> None:
        self._server_command = server_command
        self._gms_url = gms_url
        self._token = token

    @property
    def access_path(self) -> Literal["mcp", "sdk"]:
        """Report provenance: this client is the MCP path."""
        return "mcp"

    def search(
        self,
        query: str,
        entity_types: Sequence[EntityType] | None = None,
        limit: int = 25,
    ) -> tuple[EntityRef, ...]:
        """Full-text search over the catalog.

        Contract: call the server's search tool with `query`, optional entity
        type filter and `limit`; map each hit to `EntityRef`. Return an empty
        tuple for no hits, never None. Entity names are identifiers here, not
        free text, so no envelope is needed.
        """
        raise StubNotImplementedError(
            f"{_T}.search", OWNER_A, "MCP search tool -> tuple[EntityRef, ...]"
        )

    def get_entities(self, urns: Sequence[str]) -> tuple[EntityRef, ...]:
        """Resolve URNs to entities in one round trip, preserving input order.

        Contract: batch the URNs into a single tool call. Missing URNs are
        omitted from the result rather than raising, because a dangling
        downstream reference is a real state of the graph and the caller
        decides what it means.
        """
        raise StubNotImplementedError(
            f"{_T}.get_entities", OWNER_A, "MCP get_entities tool, order preserving, batched"
        )

    def list_schema_fields(self, dataset_urn: str) -> tuple[SchemaFieldInfo, ...]:
        """Return the dataset's columns.

        Contract: field descriptions MUST be returned as
        `UntrustedEnvelope`, wrapped here at the boundary. Include native type
        and nullability where the platform reports them; both feed the fix
        generator's prompt.
        """
        raise StubNotImplementedError(
            f"{_T}.list_schema_fields",
            OWNER_A,
            "MCP schema read; descriptions wrapped as UntrustedEnvelope at this boundary",
        )

    def get_lineage(
        self,
        urn: str,
        column: str | None = None,
        direction: LineageDirection = "DOWNSTREAM",
        max_hops: int = DEFAULT_MAX_HOPS,
    ) -> tuple[DownstreamEntity, ...]:
        """Walk COLUMN-LEVEL lineage and return the entities reached.

        Contract, and the most important one in this file:

        - When `column` is given, only fine-grained (column-level) edges count.
          If the server can only answer at table level, return what it can and
          make the caller emit a `column_level_lineage` degradation; do NOT
          silently widen to table-level reachability, which would inflate
          `downstream_reach` and therefore severity.
        - Every returned entity carries a complete `path` of `LineageHop`,
          including intervening transformations. A downstream entity without a
          path is not reportable: the path is what makes the finding auditable.
        - Deduplicate by URN, keeping the SHORTEST hop distance.
        - Respect `max_hops`; a cyclic graph must terminate.
        """
        raise StubNotImplementedError(
            f"{_T}.get_lineage",
            OWNER_A,
            "MCP column-level lineage, N-hop, every entity carrying its full path",
        )

    def get_lineage_paths_between(
        self,
        source_urn: str,
        target_urn: str,
        source_column: str | None = None,
        max_hops: int = DEFAULT_MAX_HOPS,
    ) -> tuple[LineagePath, ...]:
        """Return every column-level path between two entities.

        Contract: used to explain *why* a specific dashboard is affected when a
        reviewer asks. Order paths shortest first.
        """
        raise StubNotImplementedError(
            f"{_T}.get_lineage_paths_between",
            OWNER_A,
            "MCP lineage-paths-between tool -> tuple[LineagePath, ...], shortest first",
        )

    def get_dataset_queries(
        self,
        dataset_urn: str,
        window_days: int = DEFAULT_USAGE_WINDOW_DAYS,
    ) -> QueryUsage:
        """Return observed query usage for a dataset.

        Contract: when usage has not been ingested, return
        `QueryUsage(source="unavailable", query_count=0, ...)`. Do NOT return
        zero with `source="datahub_usage"`: the severity engine treats
        unavailable and zero identically for scoring but the report must not
        claim nobody queries a table when nobody measured.
        """
        raise StubNotImplementedError(
            f"{_T}.get_dataset_queries",
            OWNER_A,
            "MCP usage/queries tool; 'unavailable' is distinct from zero",
        )

    def get_owners(self, urn: str) -> tuple[Owner, ...]:
        """Return owners of an entity, including container-inherited ones.

        Contract: set `source` so the renderer can say why each person is being
        notified, and resolve `handle` to something @-mentionable where DataHub
        knows one. Deduplicate by URN.
        """
        raise StubNotImplementedError(
            f"{_T}.get_owners", OWNER_A, "MCP ownership read, deduplicated, with source set"
        )

    def get_assertions(
        self, dataset_urn: str, column: str | None = None
    ) -> tuple[AssertionRef, ...]:
        """Return assertions attached to a dataset.

        Contract: set `references_changed_column` when the assertion definition
        names the column under review. That distinguishes a guaranteed failure
        from a probable one, and the renderer says so.
        """
        raise StubNotImplementedError(
            f"{_T}.get_assertions", OWNER_A, "MCP assertions read with column reference detection"
        )

    def get_data_contracts(
        self, dataset_urn: str, column: str | None = None
    ) -> tuple[ContractRef, ...]:
        """Return data contracts attached to a dataset.

        Contract: include PENDING contracts as well as ACTIVE ones; see
        `core.datahub.base.contract_covers_column`.
        """
        raise StubNotImplementedError(
            f"{_T}.get_data_contracts", OWNER_A, "MCP data contract read, ACTIVE and PENDING"
        )
