"""DataHub access through `mcp-server-datahub`.

This is the path the hackathon is about: the same tools a DataHub agent would
call, driven from a CI job.

## What this module can and cannot answer

The tool names and argument shapes below were read out of `mcp-server-datahub`
0.6.0 — its registered tool list and the GraphQL documents in its `gql/`
directory — rather than taken from the expectations the previous version of this
docstring carried. Six of the nine reads in `DataHubReader` map onto a real
tool; the other three do not, and pretending otherwise is how two of the seven
severity factors would silently become zero:

| Read | Tool | Notes |
| --- | --- | --- |
| `search` | `search` | |
| `get_entities` | `get_entities` | |
| `list_schema_fields` | `list_schema_fields` | |
| `get_lineage` | `get_lineage` | `column` set makes it column-level |
| `get_lineage_paths_between` | `get_lineage_paths_between` | |
| `get_dataset_queries` | `get_dataset_queries` | catalogued queries, NOT usage statistics |
| `get_owners` | — | derived from `get_entities`, which carries `ownership` |
| `get_assertions` | — | Cloud-only tool, also gated behind an env flag |
| `get_data_contracts` | — | no tool, and no `.gql` in the package mentions contracts |

The last two raise `DataHubCapabilityError` rather than returning an empty
tuple. An empty tuple would be a lie with a score attached: `contract_presence`
is 12 points and `assertion_presence` 4, and "this dataset has no contract" and
"this access path cannot see contracts" must never produce the same number.
`core.datahub.hybrid.HybridDataHubReader` is what resolves it, by serving those
reads from the SDK and reporting `access_path` as `"mcp+sdk"`.

`get_dataset_queries` deserves its own warning. It calls `listQueries`, which
returns catalogued **Query entities** — the Queries tab — and not the
`datasetUsageStatistics` aspect that the `query_usage` factor is defined on.
That is a different measurement, so it is reported as `source="datahub_queries"`
and never as `datahub_usage`.

Every free-text field that comes back is wrapped with
`core.untrusted.envelope` at this boundary, not later. Once a raw `str` escapes
this module, nothing downstream can tell it apart from a trusted one.

## What a live run corrected

The first version of this module was written against the server's GraphQL
documents alone, and got the lineage response wrong in two ways that a schema
cannot show you. Both produced zero downstream entities, no error, and a
confident near-zero severity — the failure mode this repository is built to
prevent, arrived at from the inside.

1. `get_lineage` returns `{"upstreams": ..., "downstreams": ..., "metadata": ...}`
   and nests `searchResults` under the direction. This module read
   `payload["searchResults"]`, which is never present.
2. `paths` is not in the response at all. The server's
   `_extract_lineage_columns_from_paths` rebuilds every column-level result as
   `{entity, degree, lineageColumns}` and drops the path to save tokens. The
   path is reconstructed instead — see `get_lineage`.

`get_lineage_paths_between` was wrong in a third way that no run had reached
yet: it returns `paths` at the top level, and it rejects a call that sets
`source_column` without `target_column`. See `_paths_between`.

**What is NOT verified**: the reconstruction in `get_lineage` is written
against `mcp-server-datahub` 0.6.0's own source, and the degree-1 branch is the
only one a live run has exercised. The `get_lineage_paths_between` fallbacks
have not been executed against a running DataHub.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from contracts.models import (
    AssertionRef,
    ContractRef,
    DownstreamEntity,
    EntityType,
    LineageHop,
    Owner,
    OwnershipType,
    OwnerType,
    QueryUsage,
    Transformation,
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
from core.datahub.mapping import (
    column_of,
    entity_name_of,
    entity_type_of,
    platform_of,
    transformation_type_of,
    usage_from,
)
from core.datahub.mcp_session import McpSession, ToolCaller
from core.errors import DataHubAccessError, DataHubCapabilityError
from core.untrusted.envelope import UntrustedEnvelope

#: Ceiling on entities returned by one lineage call. The tool defaults to 30,
#: which silently truncates a wide fan-out; severity is computed from the count
#: of downstream entities, so a truncated read produces a confidently low score.
_LINEAGE_MAX_RESULTS = 200

#: Ceiling on catalogued queries fetched for the usage factor.
_QUERY_PAGE_SIZE = 100


def _as_list(payload: Any) -> list[dict[str, Any]]:
    """Coerce a tool result into a list of mappings, tolerating the single form."""
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _entity_display_name(entity: dict[str, Any], urn: str) -> str:
    """Best display name for an entity, falling back to the URN.

    Names come from `properties.name`, which DataHub treats as an identifier,
    not as documentation. Descriptions are deliberately not consulted here.
    """
    properties = entity.get("properties")
    if isinstance(properties, dict):
        for key in ("name", "qualifiedName"):
            value = properties.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().replace("\n", " ")[:256]
    name = entity.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip().replace("\n", " ")[:256]
    return entity_name_of(urn)


def _platform_name(entity: dict[str, Any], urn: str) -> str | None:
    """Short platform name, from the entity if present, else parsed from the URN."""
    platform = entity.get("platform")
    if isinstance(platform, dict):
        properties = platform.get("properties")
        if isinstance(properties, dict):
            display = properties.get("name")
            if isinstance(display, str) and display.strip():
                return display.strip()
        platform_urn = platform.get("urn")
        if isinstance(platform_urn, str):
            return platform_urn.rsplit(":", maxsplit=1)[-1]
    return platform_of(urn)


def _entity_ref(entity: dict[str, Any]) -> EntityRef | None:
    """Map a GraphQL entity onto `EntityRef`, or None if we do not report its type."""
    urn = entity.get("urn")
    if not isinstance(urn, str) or not urn:
        return None
    entity_type = entity_type_of(urn)
    if entity_type is None:
        return None
    url = entity.get("url")
    return EntityRef(
        urn=urn,
        entity_type=entity_type,
        name=_entity_display_name(entity, urn),
        platform=_platform_name(entity, urn),
        url=url if isinstance(url, str) else None,
    )


def _owner_type_of(urn: str) -> OwnerType:
    return "corpGroup" if urn.startswith("urn:li:corpGroup:") else "corpuser"


def _ownership_type_of(raw: Any) -> OwnershipType | None:
    """Map DataHub's ownership type onto the contract's closed set."""
    if isinstance(raw, dict):
        raw = raw.get("type") or (raw.get("info") or {}).get("name")
    if not isinstance(raw, str):
        return None
    normalized = raw.strip().upper().replace("URN:LI:OWNERSHIPTYPE:__SYSTEM__", "").strip("_")
    # Only the values the contract publishes. DataHub's own enum is wider
    # (DEVELOPER, STAKEHOLDER, CUSTOM, ...); anything outside the closed set
    # becomes None rather than being coerced to a neighbouring value, because
    # `ownership_type` is shown to a reviewer deciding who to notify.
    allowed: dict[str, OwnershipType] = {
        "TECHNICAL_OWNER": "TECHNICAL_OWNER",
        "BUSINESS_OWNER": "BUSINESS_OWNER",
        "DATA_STEWARD": "DATA_STEWARD",
        "DATAOWNER": "TECHNICAL_OWNER",
        "PRODUCER": "PRODUCER",
        "CONSUMER": "CONSUMER",
        "NONE": "NONE",
    }
    return allowed.get(normalized)


def _owner_display_name(node: dict[str, Any], urn: str) -> str:
    """Display name for an owner, never empty and never multi-line."""
    for container in ("editableProperties", "properties"):
        properties = node.get(container)
        if isinstance(properties, dict):
            for key in ("displayName", "email", "fullName"):
                value = properties.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip().replace("\n", " ")[:128]
    name = node.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip().replace("\n", " ")[:128]
    return urn.rsplit(":", maxsplit=1)[-1] or urn


class McpDataHubReader:
    """`DataHubReader` backed by mcp-server-datahub. Satisfies the protocol structurally."""

    def __init__(
        self,
        server_command: str,
        gms_url: str,
        token: str | None = None,
        session: ToolCaller | None = None,
    ) -> None:
        self._session = session or McpSession(
            server_command=server_command, gms_url=gms_url, token=token
        )
        self._notes: list[ReaderNote] = []

    @property
    def access_path(self) -> Literal["mcp", "sdk"]:
        """Report provenance: this client is the MCP path."""
        return "mcp"

    def close(self) -> None:
        """Shut the server subprocess down."""
        self._session.close()

    def drain_notes(self) -> tuple[ReaderNote, ...]:
        """Return everything this reader had to drop, and forget it.

        Draining rather than accumulating: the analyzer calls this once per
        column change, and a note about column A must not be attributed to
        column B. See `core.datahub.base.drain_reader_notes`.
        """
        notes = tuple(self._notes)
        self._notes.clear()
        return notes

    # -- reads that map onto a real tool -------------------------------------

    def search(
        self,
        query: str,
        entity_types: Sequence[EntityType] | None = None,
        limit: int = 25,
    ) -> tuple[EntityRef, ...]:
        """Full-text search over the catalog.

        Entity names are identifiers here, not free text, so no envelope is
        needed. An unreportable entity type is dropped rather than guessed at.
        """
        arguments: dict[str, Any] = {"query": query or "*", "num_results": limit}
        if entity_types:
            joined = ",".join(entity_types)
            arguments["filter"] = f"_entityType:{joined}"

        payload = self._session.call("search", arguments)
        results = (payload or {}).get("searchResults") if isinstance(payload, dict) else None
        refs = [
            ref
            for item in _as_list(results)
            if isinstance(item.get("entity"), dict)
            and (ref := _entity_ref(item["entity"])) is not None
        ]
        return tuple(refs)

    def get_entities(self, urns: Sequence[str]) -> tuple[EntityRef, ...]:
        """Resolve URNs to entities in one round trip, preserving input order.

        Missing URNs are omitted rather than raising: a dangling downstream
        reference is a real state of the graph, and the caller decides what it
        means. The server reports those as an `error` key on the entity.
        """
        wanted = [urn for urn in urns if urn]
        if not wanted:
            return ()

        payload = self._session.call("get_entities", {"urns": wanted})
        by_urn: dict[str, EntityRef] = {}
        for entity in _as_list(payload):
            if entity.get("error"):
                continue
            ref = _entity_ref(entity)
            if ref is not None:
                by_urn[ref.urn] = ref
        return tuple(by_urn[urn] for urn in wanted if urn in by_urn)

    def list_schema_fields(self, dataset_urn: str) -> tuple[SchemaFieldInfo, ...]:
        """Return the dataset's columns, descriptions wrapped at this boundary."""
        payload = self._session.call("list_schema_fields", {"urn": dataset_urn, "limit": 500})
        fields = (payload or {}).get("fields") if isinstance(payload, dict) else None

        collected: list[SchemaFieldInfo] = []
        for field in _as_list(fields):
            field_path = field.get("fieldPath")
            if not isinstance(field_path, str) or not field_path:
                continue
            description = field.get("description")
            collected.append(
                SchemaFieldInfo(
                    field_path=field_path,
                    native_type=field.get("nativeDataType")
                    if isinstance(field.get("nativeDataType"), str)
                    else None,
                    nullable=field.get("nullable")
                    if isinstance(field.get("nullable"), bool)
                    else None,
                    is_primary_key=field.get("isPartOfKey")
                    if isinstance(field.get("isPartOfKey"), bool)
                    else None,
                    # Wrapped HERE, at the boundary, exactly as the SDK path
                    # does it. Once this escapes as a plain str nothing
                    # downstream can tell it from a trusted one.
                    description=UntrustedEnvelope.from_text(
                        description,
                        field=f"{dataset_urn}.{field_path}.description",
                        source="datahub_schema_field_description",
                    )
                    if isinstance(description, str) and description.strip()
                    else None,
                )
            )
        return tuple(collected)

    def get_lineage(
        self,
        urn: str,
        column: str | None = None,
        direction: LineageDirection = "DOWNSTREAM",
        max_hops: int = DEFAULT_MAX_HOPS,
    ) -> tuple[DownstreamEntity, ...]:
        """Walk COLUMN-LEVEL lineage and return the entities reached.

        `column` is passed straight through: the server restricts the walk to
        fine-grained edges when it is set. Entities are deduplicated by URN
        keeping the shortest hop distance.

        ## Where the path comes from, and why it is not in this response

        `get_lineage` cannot supply a lineage path. `_extract_lineage_columns_from_paths`
        in the server's `tools/lineage.py` rebuilds every column-level search
        result as `{entity, degree, lineageColumns}` and **discards `paths`** to
        save tokens. So the path has to be reconstructed, and the cheapest
        honest reconstruction depends on the degree:

        * `degree == 1` — the entity is directly adjacent, so a single hop from
          the changed column to `lineageColumns[0]` is a true statement about
          the graph, and no further call is needed.
        * `degree >= 2` — intermediates exist and this response does not name
          them. A single hop would assert an edge that is not there, so the real
          path is fetched with `get_lineage_paths_between`, and `hop_distance`
          is taken from that path rather than from `degree`. That also sidesteps
          `degree` being inflated by sibling routing: a dbt model ingested as
          both a `dbt` and a `duckdb` dataset reports its logical hop-1 consumer
          at degree 2, because the walk passes through the sibling.

        When the column-level path cannot be fetched, a dataset-level one is
        tried: its hops are real, they just carry no column annotations, which
        `LineageHop` already models as `from_column`/`to_column` of `None` and
        the analyzer already degrades on. When that fails too, the entity is
        dropped — an entity without a path is not reportable — and a
        `ReaderNote` names it so the drop reaches the report instead of
        quietly lowering the score.
        """
        arguments: dict[str, Any] = {
            "urn": urn,
            "upstream": direction == "UPSTREAM",
            "max_hops": max_hops,
            "max_results": _LINEAGE_MAX_RESULTS,
        }
        if column:
            arguments["column"] = column

        payload = self._session.call("get_lineage", arguments)
        results = _direction_results(payload, direction)

        best: dict[str, DownstreamEntity] = {}
        unproven: list[str] = []
        for item in _as_list(results):
            entity = item.get("entity")
            if not isinstance(entity, dict):
                continue
            ref = _entity_ref(entity)
            if ref is None or ref.urn == urn:
                continue

            resolved = self._resolve_path(item, source_urn=urn, source_column=column, target=ref)
            if resolved is None:
                if ref.urn not in unproven:
                    unproven.append(ref.urn)
                continue
            hops, distance = resolved

            existing = best.get(ref.urn)
            if existing is not None and existing.hop_distance <= distance:
                continue
            best[ref.urn] = DownstreamEntity(
                urn=ref.urn,
                entity_type=ref.entity_type,
                name=ref.name,
                hop_distance=distance,
                path=hops,
                platform=ref.platform,
                via_column=hops[-1].to_column,
                url=ref.url,
            )

        # Only entities that never resolved by any route. One reached twice, at
        # degree 1 and degree 3, is proven and must not be reported as a gap.
        still_unproven = [candidate for candidate in unproven if candidate not in best]
        if still_unproven:
            self._note_unproven(still_unproven, source_urn=urn, column=column)
        return tuple(sorted(best.values(), key=lambda e: (e.hop_distance, e.urn)))

    def _resolve_path(
        self,
        item: dict[str, Any],
        source_urn: str,
        source_column: str | None,
        target: EntityRef,
    ) -> tuple[tuple[LineageHop, ...], int] | None:
        """Return the path to one reached entity and its hop distance, or None.

        None means no route could be demonstrated by any available call, which
        is the caller's cue to drop the entity and record a note.
        """
        degree = item.get("degree")
        distance = degree if isinstance(degree, int) and degree >= 1 else None
        target_column = _first_lineage_column(item)

        # Directly adjacent: this response already contains the whole edge.
        if distance == 1:
            return (
                (
                    LineageHop(
                        from_urn=source_urn,
                        to_urn=target.urn,
                        transformation=Transformation(type=transformation_type_of(None)),
                        from_column=source_column,
                        to_column=target_column,
                    ),
                ),
                1,
            )

        # Two or more hops, or a degree the server did not report: the
        # intermediates are real and unknown, so fetch the path that names them.
        for column_pair in ((source_column, target_column), (None, None)):
            paths = self._paths_between(
                source_urn, target.urn, source_column=column_pair[0], target_column=column_pair[1]
            )
            if paths:
                hops = paths[0].hops
                return hops, len(hops)
        return None

    def _note_unproven(self, urns: list[str], source_urn: str, column: str | None) -> None:
        """Record entities dropped for want of a demonstrable path."""
        named = ", ".join(urns[:5]) + (f", and {len(urns) - 5} more" if len(urns) > 5 else "")
        self._notes.append(
            ReaderNote(
                capability="column_level_lineage",
                reason=(
                    f"{len(urns)} entity(ies) reported downstream of "
                    f"{source_urn}{'.' + column if column else ''} were dropped because no "
                    f"lineage path could be reconstructed for them: {named}"
                )[:512],
                consequence=(
                    "Those entities are absent from downstream reach and from the score. "
                    "Their absence is not a finding of 'no impact' — DataHub reported them "
                    "as downstream and could not show the route, so they could not be "
                    "reported as evidence. The real severity is at least what is printed."
                ),
            )
        )

    def get_lineage_paths_between(
        self,
        source_urn: str,
        target_urn: str,
        source_column: str | None = None,
        max_hops: int = DEFAULT_MAX_HOPS,
    ) -> tuple[LineagePath, ...]:
        """Return every column-level path between two entities, shortest first.

        `max_hops` is accepted for protocol compatibility and not sent: the tool
        takes no such argument and walks with its own bound of 10.
        """
        return self._paths_between(
            source_urn, target_urn, source_column=source_column, target_column=source_column
        )

    def _paths_between(
        self,
        source_urn: str,
        target_urn: str,
        source_column: str | None,
        target_column: str | None,
    ) -> tuple[LineagePath, ...]:
        """Call `get_lineage_paths_between` and map its payload onto `LineagePath`.

        Three properties of the tool that the shape of this method is dictated
        by, all read out of `mcp-server-datahub` 0.6.0's `tools/lineage.py`:

        1. `paths` is at the TOP level of the result, next to `pathCount` and
           `metadata`. It is not under `searchResults`.
        2. The tool raises `ValueError` unless `source_column` and
           `target_column` are BOTH set or BOTH absent. Passing only the source
           column — which is the natural thing to want — fails every time.
        3. "No path exists" arrives as an `ItemNotFoundError`, which reaches us
           as a failed tool call. That is an answer, not an outage, so it
           returns `()` here rather than propagating as a read failure.
        """
        if (source_column is None) != (target_column is None):
            source_column = target_column = None

        arguments: dict[str, Any] = {
            "source_urn": source_urn,
            "target_urn": target_urn,
            "direction": "downstream",
        }
        if source_column and target_column:
            arguments["source_column"] = source_column
            arguments["target_column"] = target_column

        try:
            payload = self._session.call("get_lineage_paths_between", arguments)
        except DataHubAccessError:
            return ()
        if not isinstance(payload, dict):
            return ()

        paths: list[LineagePath] = []
        for raw in _as_list(payload.get("paths")):
            hops = _hops_from_path_nodes(
                raw.get("path"), source_urn=source_urn, target_urn=target_urn
            )
            if hops:
                paths.append(LineagePath(source_urn=source_urn, target_urn=target_urn, hops=hops))
        return tuple(sorted(paths, key=lambda p: p.hop_distance))

    def get_dataset_queries(
        self,
        dataset_urn: str,
        window_days: int = DEFAULT_USAGE_WINDOW_DAYS,
    ) -> QueryUsage:
        """Return catalogued query usage for a dataset.

        This reads `listQueries` — the Query entities DataHub has catalogued —
        which is NOT the `datasetUsageStatistics` aspect. It is therefore
        reported as `source="datahub_queries"`. When the server returns nothing
        the result is `unavailable` rather than a measured zero: the severity
        engine scores the two identically and the report must not claim nobody
        queries a table when nobody measured.
        """
        payload = self._session.call(
            "get_dataset_queries",
            {"urn": dataset_urn, "start": 0, "count": _QUERY_PAGE_SIZE},
        )
        if not isinstance(payload, dict):
            return usage_from(window_days, None)

        total = payload.get("total")
        if not isinstance(total, int):
            queries = payload.get("queries")
            total = len(_as_list(queries)) if queries is not None else None
        if total is None:
            return usage_from(window_days, None)

        usage = usage_from(window_days, total)
        return usage.model_copy(update={"source": "datahub_queries"})

    def get_owners(self, urn: str) -> tuple[Owner, ...]:
        """Return owners of an entity, deduplicated, with `source` set.

        `mcp-server-datahub` has no ownership tool; ownership arrives on the
        entity itself, which `get_entities` already fetches.
        """
        payload = self._session.call("get_entities", {"urns": [urn]})
        entities = _as_list(payload)
        if not entities or entities[0].get("error"):
            return ()

        ownership = entities[0].get("ownership")
        entries = ownership.get("owners") if isinstance(ownership, dict) else None
        owners: dict[str, Owner] = {}
        for entry in _as_list(entries):
            node = entry.get("owner")
            if not isinstance(node, dict):
                continue
            owner_urn = node.get("urn")
            if not isinstance(owner_urn, str) or not owner_urn.startswith(
                ("urn:li:corpuser:", "urn:li:corpGroup:")
            ):
                continue
            if owner_urn in owners:
                continue
            owners[owner_urn] = Owner(
                urn=owner_urn,
                owner_type=_owner_type_of(owner_urn),
                display_name=_owner_display_name(node, owner_urn),
                handle=owner_urn.rsplit(":", maxsplit=1)[-1] or None,
                ownership_type=_ownership_type_of(entry.get("ownershipType") or entry.get("type")),
                source="changed_dataset",
                for_urn=urn,
            )
        return tuple(owners.values())

    # -- reads mcp-server-datahub does not expose ----------------------------

    def get_assertions(
        self, dataset_urn: str, column: str | None = None
    ) -> tuple[AssertionRef, ...]:
        """Not available over MCP. See `DataHubCapabilityError` below.

        Contract unchanged: an implementation must set
        `references_changed_column` when the assertion names the column under
        review. `mcp-server-datahub` 0.6.0 exposes `get_dataset_assertions` only
        on DataHub Cloud (`min_version(cloud="0.3.16")`, no OSS minimum) and only
        when started with `DATA_QUALITY_TOOLS_ENABLED=true`, so on an
        open-source deployment there is no tool to call.

        Raises rather than returning `()` because an empty tuple is a scored
        claim: it would zero `assertion_presence` and read as "this dataset has
        no assertions".
        """
        msg = (
            "mcp-server-datahub does not expose assertions on an open-source DataHub: "
            "get_dataset_assertions is a DataHub Cloud tool and is additionally gated "
            "behind DATA_QUALITY_TOOLS_ENABLED. Use the hybrid reader, which serves "
            "this read from the Python SDK, or BLAST_RADIUS_DATAHUB_MODE=sdk."
        )
        raise DataHubCapabilityError(msg)

    def get_data_contracts(
        self, dataset_urn: str, column: str | None = None
    ) -> tuple[ContractRef, ...]:
        """Not available over MCP. See `get_assertions` for the reasoning.

        Contract unchanged: an implementation must include PENDING contracts as
        well as ACTIVE ones. `mcp-server-datahub` 0.6.0 has no data-contract
        tool and no GraphQL document mentioning contracts, so there is nothing
        to call.
        """
        msg = (
            "mcp-server-datahub has no data-contract tool. Use the hybrid reader, "
            "which serves this read from the Python SDK, or BLAST_RADIUS_DATAHUB_MODE=sdk."
        )
        raise DataHubCapabilityError(msg)


# -- lineage path reconstruction ---------------------------------------------


def _direction_results(payload: Any, direction: LineageDirection) -> Any:
    """Pull the search results for one direction out of a `get_lineage` payload.

    The tool returns `{"upstreams": {...}, "downstreams": {...}, "metadata": ...}`
    and puts `searchResults` inside the direction it was asked for — see
    `AssetLineageAPI.get_lineage`. Reading `searchResults` off the top level, as
    this module did, finds nothing on every catalog and every query, and
    produces zero downstream entities with no error anywhere.

    The top level is still checked as a fallback, so that a future server that
    flattens the response does not silently break this again in the other
    direction.
    """
    if not isinstance(payload, dict):
        return None
    key = "upstreams" if direction == "UPSTREAM" else "downstreams"
    section = payload.get(key)
    if isinstance(section, dict) and section.get("searchResults") is not None:
        return section.get("searchResults")
    return payload.get("searchResults")


def _first_lineage_column(item: dict[str, Any]) -> str | None:
    """Return the downstream column this result carries the change into.

    `lineageColumns` is what the server substitutes for `paths` on a
    column-level walk: the `fieldPath` of the last node of each discarded path,
    which is to say the column in the reached dataset. The first entry is used
    because `DownstreamEntity.via_column` holds one column; the rest are
    reachable through `get_lineage_paths_between` when the detail is wanted.
    """
    for value in _as_list_of_str(item.get("lineageColumns")):
        return value
    return None


def _as_list_of_str(value: Any) -> list[str]:
    """Coerce a tool result field into a list of non-empty strings."""
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [entry for entry in value if isinstance(entry, str) and entry]
    return []


def _hops_from_path_nodes(nodes: Any, source_urn: str, target_urn: str) -> tuple[LineageHop, ...]:
    """Turn a GraphQL lineage path into `LineageHop`s.

    The path alternates dataset and schemaField entities. Column-level runs
    carry `SchemaFieldEntity` nodes with `fieldPath` and a `parent`, so each hop
    is collapsed onto the dataset pair it connects while keeping the columns.

    DataHub's path does not carry `transformOperation`, so the transformation
    type is `unknown` rather than guessed: a confidently wrong transformation
    label is shown to a reviewer and is worse than an honest blank.
    """
    steps: list[tuple[str, str | None]] = []
    for node in _as_list(nodes):
        urn = node.get("urn")
        if not isinstance(urn, str) or not urn:
            continue
        field_path = node.get("fieldPath")
        if isinstance(field_path, str) and field_path:
            parent = node.get("parent")
            parent_urn = parent.get("urn") if isinstance(parent, dict) else None
            dataset_urn = parent_urn if isinstance(parent_urn, str) else None
            if dataset_urn is None:
                split = column_of(urn)
                dataset_urn = (
                    urn.rsplit(",", maxsplit=1)[0].removeprefix("urn:li:schemaField:(")
                    if split
                    else None
                )
            if dataset_urn:
                steps.append((dataset_urn, field_path))
            continue
        if entity_type_of(urn) is not None:
            steps.append((urn, None))

    # Collapse consecutive entries for the same dataset, keeping any column.
    collapsed: list[tuple[str, str | None]] = []
    for urn, column in steps:
        if collapsed and collapsed[-1][0] == urn:
            if column and not collapsed[-1][1]:
                collapsed[-1] = (urn, column)
            continue
        collapsed.append((urn, column))

    if collapsed and collapsed[0][0] != source_urn:
        collapsed.insert(0, (source_urn, None))
    if collapsed and collapsed[-1][0] != target_urn:
        collapsed.append((target_urn, None))
    if len(collapsed) < 2:
        return ()

    return tuple(
        LineageHop(
            from_urn=collapsed[index][0],
            to_urn=collapsed[index + 1][0],
            transformation=Transformation(type=transformation_type_of(None)),
            from_column=collapsed[index][1],
            to_column=collapsed[index + 1][1],
        )
        for index in range(len(collapsed) - 1)
    )
