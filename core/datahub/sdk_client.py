"""DataHub access through the Python SDK (`acryl-datahub`).

The second of the two supported access paths. It exists for three reasons:

1. a CI container that cannot run an MCP subprocess still needs to work;
2. some reads are cheaper as a single GraphQL query than as a tool call loop;
3. when a mutation tool is missing, `core.writeback` falls back to the SDK, and
   a fallback whose read path was never exercised is not a fallback.

The two clients must be behaviourally interchangeable. Any difference a caller
can observe is a bug in one of them, not a feature of either. OWNER A should run
the same test suite against both.

## How this is built, and what that buys

Everything on the analysis critical path goes through the SDK's TYPED aspect
classes (`SchemaMetadataClass`, `UpstreamLineageClass`, `OwnershipClass`, ...)
rather than through hand-written GraphQL. The field names in a typed class are
checked by mypy against the installed `acryl-datahub`, so a version bump that
renames something fails `make typecheck` instead of failing in front of a
reviewer.

The translation from those aspects to the contract models lives in
`core.datahub.mapping`, is pure, and is unit-tested without a server. What is
left here is I/O and traversal.

**What is NOT verified**: nothing in this module has been executed against a
running DataHub in this repository, because bringing one up needs Docker. The
shapes are taken from the installed SDK's own type definitions, which is the
best available offline evidence and is not the same as evidence.
`blast-radius doctor` is the command that turns that into a real answer.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any, Literal

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
    SchemaFieldInfo,
)
from core.datahub.mapping import (
    assertion_type_of,
    column_of,
    contract_state_of,
    dataset_name_of,
    entity_name_of,
    entity_type_of,
    platform_of,
    references_column,
    schema_field_urn,
    transformation_type_of,
    usage_from,
)
from core.errors import DataHubAccessError
from core.untrusted.envelope import UntrustedEnvelope

if TYPE_CHECKING:  # pragma: no cover - typing only
    from datahub.ingestion.graph.client import DataHubGraph

_T = "core.datahub.sdk_client.SdkDataHubReader"

#: Relationship emitted by a dataset's `upstreamLineage` aspect. Walking it
#: INCOMING from X finds the datasets that declare X as an upstream — which is
#: to say, X's downstream consumers.
_DOWNSTREAM_OF = "DownstreamOf"

#: Relationship from an assertion to the dataset it asserts on.
_ASSERTS = "Asserts"

#: Relationship from a data contract to the dataset it governs.
_CONTRACT_FOR = "ContractFor"

_MILLISECONDS_PER_DAY = 86_400_000

#: Ceiling on usage buckets fetched in one `getTimeseriesAspectValues` call.
#:
#: Required, not tuning: GMS NPEs on a request that omits it. The value covers
#: daily buckets across the widest window `QueryUsage` permits (365) with room
#: for the weekly and monthly rollups that share the aspect, so the sum is over
#: the whole window rather than over a silently truncated page — the same
#: failure `_LINEAGE_MAX_RESULTS` exists to prevent on the other read.
_USAGE_ASPECT_LIMIT = 500

#: Bounds on a single GMS request, and on how hard the SDK retries one.
#:
#: Both are set explicitly because the SDK's defaults are unbounded, and an
#: unreachable DataHub then turns `blast-radius analyze` into a command that
#: appears to hang for minutes before failing. In a pull-request check, a review
#: that fails in ten seconds with "GMS is unreachable" is worth far more than one
#: that eventually says the same thing after the reviewer has given up.
_REQUEST_TIMEOUT_SECONDS = 15
_RETRY_MAX_TIMES = 1


class SdkDataHubReader:
    """`DataHubReader` backed by acryl-datahub. Satisfies the protocol structurally."""

    def __init__(self, gms_url: str, token: str | None = None) -> None:
        self._gms_url = gms_url
        self._token = token
        self._graph_cache: DataHubGraph | None = None

    @property
    def access_path(self) -> Literal["mcp", "sdk"]:
        """Report provenance: this client is the SDK path."""
        return "sdk"

    # -- connection ----------------------------------------------------------

    @property
    def _graph(self) -> DataHubGraph:
        """One client per reader, built on first use.

        Imported lazily so that the base install — which does not carry the
        `datahub` extra — can still run `blast-radius stubs` and `doctor`.
        """
        if self._graph_cache is not None:
            return self._graph_cache
        try:
            from datahub.ingestion.graph.client import DatahubClientConfig, DataHubGraph
        except ImportError as exc:
            msg = (
                "the `datahub` extra is not installed, so the SDK access path is "
                "unavailable. Install it with `uv sync --extra datahub`."
            )
            raise DataHubAccessError(msg) from exc

        try:
            self._graph_cache = DataHubGraph(
                DatahubClientConfig(
                    server=self._gms_url,
                    token=self._token,
                    timeout_sec=_REQUEST_TIMEOUT_SECONDS,
                    retry_max_times=_RETRY_MAX_TIMES,
                )
            )
        except Exception as exc:
            msg = f"could not connect to DataHub at {self._gms_url}: {exc}"
            raise DataHubAccessError(msg) from exc
        return self._graph_cache

    def _aspect(self, urn: str, aspect_type: Any) -> Any:
        """Read one aspect, turning any transport failure into DataHubAccessError."""
        try:
            return self._graph.get_aspect(entity_urn=urn, aspect_type=aspect_type)
        except Exception as exc:
            msg = f"reading {aspect_type.__name__} of {urn} failed: {exc}"
            raise DataHubAccessError(msg) from exc

    def _related(self, urn: str, relationship: str, *, incoming: bool) -> tuple[str, ...]:
        """Return the URNs related to `urn` by one relationship type."""
        from datahub.ingestion.graph.openapi import RelationshipDirection

        direction = RelationshipDirection.INCOMING if incoming else RelationshipDirection.OUTGOING
        try:
            related = self._graph.get_related_entities(urn, [relationship], direction)
            return tuple(entity.urn for entity in related)
        except Exception as exc:
            msg = f"reading {relationship} relationships of {urn} failed: {exc}"
            raise DataHubAccessError(msg) from exc

    # -- search and identity -------------------------------------------------

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
        graphql = """
        query blastRadiusSearch($input: SearchAcrossEntitiesInput!) {
          searchAcrossEntities(input: $input) {
            searchResults { entity { urn type } }
          }
        }
        """
        variables: dict[str, Any] = {"input": {"query": query, "start": 0, "count": limit}}
        if entity_types:
            variables["input"]["types"] = [t.upper() for t in entity_types]

        try:
            payload = self._graph.execute_graphql(graphql, variables=variables)
        except Exception as exc:
            msg = f"search for {query!r} failed: {exc}"
            raise DataHubAccessError(msg) from exc

        results = (payload.get("searchAcrossEntities") or {}).get("searchResults") or []
        return tuple(
            reference
            for result in results
            if (reference := self._reference_for((result.get("entity") or {}).get("urn")))
        )

    def get_entities(self, urns: Sequence[str]) -> tuple[EntityRef, ...]:
        """Resolve URNs to entities in one round trip, preserving input order.

        No round trip is needed: a DataHub URN carries the entity type, the
        platform and the name, so resolving it is parsing rather than fetching.
        A missing URN is omitted rather than raising — a dangling downstream
        reference is a real state of the graph, and the caller decides what it
        means.
        """
        return tuple(
            reference for urn in urns if (reference := self._reference_for(urn)) is not None
        )

    def _reference_for(self, urn: str | None) -> EntityRef | None:
        if not urn:
            return None
        entity_type = entity_type_of(urn)
        if entity_type is None:
            return None
        return EntityRef(
            urn=urn,
            entity_type=entity_type,
            name=entity_name_of(urn),
            platform=platform_of(urn),
        )

    # -- schema --------------------------------------------------------------

    def list_schema_fields(self, dataset_urn: str) -> tuple[SchemaFieldInfo, ...]:
        """Return the dataset's columns, descriptions wrapped as untrusted.

        Contract: read the `schemaMetadata` aspect. Field descriptions MUST be
        wrapped with `core.untrusted.envelope` before leaving this method.
        """
        from datahub.metadata.schema_classes import SchemaMetadataClass

        schema = self._aspect(dataset_urn, SchemaMetadataClass)
        if schema is None:
            return ()

        return tuple(
            SchemaFieldInfo(
                field_path=field.fieldPath,
                native_type=field.nativeDataType,
                nullable=field.nullable,
                is_primary_key=field.isPartOfKey,
                # Wrapped HERE, at the boundary. Once this escapes as a plain
                # str nothing downstream can tell it from a trusted one.
                description=(
                    UntrustedEnvelope.from_text(
                        field.description,
                        field=f"{dataset_urn}.{field.fieldPath}.description",
                        source="datahub_schema_field_description",
                    )
                    if field.description
                    else None
                ),
            )
            for field in (schema.fields or [])
        )

    # -- lineage -------------------------------------------------------------

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
        if direction != "DOWNSTREAM":
            msg = "upstream traversal is not used by the analysis and is not implemented"
            raise DataHubAccessError(msg)

        reached: dict[str, DownstreamEntity] = {}
        # (dataset urn, column, path taken to get here). Breadth-first, so the
        # first time an entity is seen is at its shortest distance.
        frontier: list[tuple[str, str | None, tuple[LineageHop, ...]]] = [(urn, column, ())]
        visited: set[tuple[str, str | None]] = {(urn, column)}

        for hop_distance in range(1, max_hops + 1):
            next_frontier: list[tuple[str, str | None, tuple[LineageHop, ...]]] = []

            for source_urn, source_column, path_so_far in frontier:
                for consumer_urn in self._related(source_urn, _DOWNSTREAM_OF, incoming=True):
                    for hop in self._hops_between(source_urn, source_column, consumer_urn):
                        path = (*path_so_far, hop)
                        key = (consumer_urn, hop.to_column)
                        if consumer_urn not in reached:
                            entity_type = entity_type_of(consumer_urn)
                            if entity_type is not None:
                                reached[consumer_urn] = DownstreamEntity(
                                    urn=consumer_urn,
                                    entity_type=entity_type,
                                    name=entity_name_of(consumer_urn),
                                    platform=platform_of(consumer_urn),
                                    hop_distance=hop_distance,
                                    via_column=hop.to_column,
                                    path=path,
                                )
                        if key not in visited:
                            visited.add(key)
                            next_frontier.append((consumer_urn, hop.to_column, path))

            frontier = next_frontier
            if not frontier:
                break

        return tuple(reached.values())

    def _hops_between(
        self,
        source_urn: str,
        source_column: str | None,
        consumer_urn: str,
    ) -> tuple[LineageHop, ...]:
        """Return the column-level edges from `source_urn` into `consumer_urn`.

        Reads the CONSUMER's `upstreamLineage`, because that is where DataHub
        records fine-grained edges: a dataset declares what it reads, not what
        reads it.

        Returns an empty tuple when the consumer has no fine-grained edge from
        this column. That is what keeps a table-level-only catalog from being
        reported as if it had been analysed column by column: no column-level
        edge means no downstream entity, and the caller emits a
        `column_level_lineage` degradation rather than widening the search.
        """
        from datahub.metadata.schema_classes import UpstreamLineageClass

        lineage = self._aspect(consumer_urn, UpstreamLineageClass)
        if lineage is None:
            return ()

        wanted = schema_field_urn(source_urn, source_column) if source_column else None
        hops: list[LineageHop] = []

        for edge in lineage.fineGrainedLineages or []:
            upstreams = list(edge.upstreams or [])
            matched = [
                candidate
                for candidate in upstreams
                if (wanted is not None and candidate == wanted)
                or (wanted is None and candidate.startswith(f"urn:li:schemaField:({source_urn},"))
            ]
            if not matched:
                continue
            for downstream in edge.downstreams or []:
                if not downstream.startswith(f"urn:li:schemaField:({consumer_urn},"):
                    continue
                hops.append(
                    LineageHop(
                        from_urn=source_urn,
                        to_urn=consumer_urn,
                        from_column=column_of(matched[0]),
                        to_column=column_of(downstream),
                        transformation=Transformation(
                            type=transformation_type_of(edge.transformOperation),
                            expression=edge.transformOperation,
                        ),
                    )
                )
        return tuple(hops)

    def get_lineage_paths_between(
        self,
        source_urn: str,
        target_urn: str,
        source_column: str | None = None,
        max_hops: int = DEFAULT_MAX_HOPS,
    ) -> tuple[LineagePath, ...]:
        """Return every column-level path between two entities, shortest first."""
        paths = [
            LineagePath(source_urn=source_urn, target_urn=target_urn, hops=entity.path)
            for entity in self.get_lineage(source_urn, column=source_column, max_hops=max_hops)
            if entity.urn == target_urn
        ]
        return tuple(sorted(paths, key=lambda p: p.hop_distance))

    # -- usage ---------------------------------------------------------------

    def get_dataset_queries(
        self,
        dataset_urn: str,
        window_days: int = DEFAULT_USAGE_WINDOW_DAYS,
    ) -> QueryUsage:
        """Return observed query usage for a dataset.

        Contract: read the `datasetUsageStatistics` aspect over `window_days`.
        Return `source="unavailable"` when the aspect is absent.

        ## Why this does not use `DataHubGraph.get_usage_aspects_from_urn`

        That helper POSTs `getTimeseriesAspectValues` without a `limit` field.
        GMS dereferences it unconditionally and answers HTTP 500:

            java.lang.NullPointerException: Cannot invoke
            "java.lang.Integer.intValue()" because "limit" is null

        The helper then logs the non-200 at DEBUG and returns `None`, which is
        the same value it returns for a dataset nobody has ever queried. So the
        report said "DataHub has no usage statistics for this dataset in the
        window" against a catalog holding thirty days of them, scored
        `query_usage` zero, and emitted a degradation whose reason was false.

        The degradation machinery worked perfectly on top of a read that lied,
        which is the point: a read that cannot tell "no data" from "request
        rejected" cannot support the floor-vs-measurement claim the report
        makes. So the request is issued here with a `limit`, and a non-200
        raises instead of quietly becoming absence.
        """
        import json
        import time

        from datahub.metadata.schema_classes import DatasetUsageStatisticsClass

        now_ms = int(time.time() * 1000)
        start_ms = now_ms - window_days * _MILLISECONDS_PER_DAY

        body = {
            "urn": dataset_urn,
            "entity": "dataset",
            "aspect": "datasetUsageStatistics",
            "startTimeMillis": start_ms,
            "endTimeMillis": now_ms,
            "limit": _USAGE_ASPECT_LIMIT,
        }
        url = f"{self._graph.config.server}/aspects?action=getTimeseriesAspectValues"

        try:
            # `_session` rather than a bare `requests.post`: it carries the
            # token, the timeout and the retry policy configured above, and
            # re-deriving those here would be a second, subtly different client.
            response = self._graph._session.post(url, data=json.dumps(body))
        except Exception as exc:
            msg = f"reading usage statistics of {dataset_urn} failed: {exc}"
            raise DataHubAccessError(msg) from exc

        if response.status_code != 200:
            msg = (
                f"reading usage statistics of {dataset_urn} failed: GMS answered "
                f"HTTP {response.status_code}. This is a rejected request, not an "
                f"absence of usage data."
            )
            raise DataHubAccessError(msg)

        try:
            values = response.json().get("value", {}).get("values", [])
        except Exception as exc:
            msg = f"usage statistics of {dataset_urn} were not decodable JSON: {exc}"
            raise DataHubAccessError(msg) from exc

        buckets = []
        for value in values:
            aspect = (value or {}).get("aspect") or {}
            if not aspect.get("value"):
                continue
            try:
                buckets.append(
                    DatasetUsageStatisticsClass.from_obj(json.loads(aspect["value"]), tuples=True)
                )
            except Exception as exc:
                msg = f"a usage bucket of {dataset_urn} could not be parsed: {exc}"
                raise DataHubAccessError(msg) from exc

        if not buckets:
            # Nothing was ingested. NOT the same as "nobody queries this", and
            # now genuinely distinguishable from it, because a rejected request
            # raised above rather than arriving here.
            return usage_from(window_days, total_queries=None)

        total = sum(bucket.totalSqlQueries or 0 for bucket in buckets)
        unique_users = max((bucket.uniqueUserCount or 0 for bucket in buckets), default=0)
        latest = max((bucket.timestampMillis or 0 for bucket in buckets), default=0)
        last_queried = (
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(latest / 1000)) if latest else None
        )
        return usage_from(window_days, total, unique_users or None, last_queried)

    # -- ownership -----------------------------------------------------------

    def get_owners(self, urn: str) -> tuple[Owner, ...]:
        """Return owners of an entity, including container-inherited ones.

        Contract: read the `ownership` aspect, then the container's, marking
        inherited owners with `source="container_inherited"`.
        """
        from datahub.metadata.schema_classes import ContainerClass

        owners = list(self._owners_of(urn, urn, source=None))

        container = self._aspect(urn, ContainerClass)
        if container is not None and container.container:
            owners.extend(
                self._owners_of(
                    container.container, container.container, source="container_inherited"
                )
            )

        seen: dict[str, Owner] = {}
        for owner in owners:
            if owner.urn not in seen:
                seen[owner.urn] = owner
        return tuple(seen.values())

    def _owners_of(self, urn: str, for_urn: str, source: str | None) -> Iterable[Owner]:
        from datahub.metadata.schema_classes import OwnershipClass

        ownership = self._aspect(urn, OwnershipClass)
        if ownership is None:
            return

        for entry in ownership.owners or []:
            owner_urn = entry.owner
            if not owner_urn.startswith(("urn:li:corpuser:", "urn:li:corpGroup:")):
                continue
            owner_type: OwnerType = (
                "corpuser" if owner_urn.startswith("urn:li:corpuser:") else "corpGroup"
            )
            identifier = owner_urn.rsplit(":", maxsplit=1)[-1]
            yield Owner(
                urn=owner_urn,
                owner_type=owner_type,
                display_name=identifier or owner_urn,
                handle=f"@{identifier}" if identifier else None,
                ownership_type=_ownership_type_of(entry),
                source=source,  # type: ignore[arg-type]
                for_urn=for_urn,
            )

    # -- assertions and contracts -------------------------------------------

    def get_assertions(
        self, dataset_urn: str, column: str | None = None
    ) -> tuple[AssertionRef, ...]:
        """Return assertions attached to a dataset, with column reference detection."""
        from datahub.metadata.schema_classes import AssertionInfoClass

        assertions: list[AssertionRef] = []
        for assertion_urn in self._related(dataset_urn, _ASSERTS, incoming=True):
            info = self._aspect(assertion_urn, AssertionInfoClass)
            if info is None:
                continue
            fields = _asserted_fields(info)
            assertions.append(
                AssertionRef(
                    urn=assertion_urn,
                    entity_urn=dataset_urn,
                    assertion_type=assertion_type_of(getattr(info, "type", None)),
                    description=getattr(info, "description", None),
                    last_result=None,
                    references_changed_column=(
                        references_column(fields, dataset_urn, column)
                        if column is not None
                        else None
                    ),
                )
            )
        return tuple(assertions)

    def get_data_contracts(
        self, dataset_urn: str, column: str | None = None
    ) -> tuple[ContractRef, ...]:
        """Return data contracts attached to a dataset, ACTIVE and PENDING."""
        from datahub.metadata.schema_classes import (
            DataContractPropertiesClass,
            DataContractStatusClass,
        )

        contracts: list[ContractRef] = []
        for contract_urn in self._related(dataset_urn, _CONTRACT_FOR, incoming=True):
            properties = self._aspect(contract_urn, DataContractPropertiesClass)
            if properties is None:
                continue
            status = self._aspect(contract_urn, DataContractStatusClass)
            fields = self._contract_fields(properties) if column is not None else None
            contracts.append(
                ContractRef(
                    urn=contract_urn,
                    entity_urn=dataset_urn,
                    state=contract_state_of(getattr(status, "state", None)),
                    name=dataset_name_of(dataset_urn),
                    # `None` when the contract's terms could not be resolved to
                    # any field: unmeasured, not measured-absent. It scores the
                    # same but `unknown_coverage` fires on it, so the report
                    # degrades instead of asserting a no.
                    references_changed_column=(
                        references_column(fields, dataset_urn, column)
                        if column is not None and fields is not None
                        else None
                    ),
                )
            )
        return tuple(contracts)

    def _contract_fields(self, properties: Any) -> tuple[str | None, ...] | None:
        """Resolve a contract's terms to the schema fields they govern.

        `DataContractPropertiesClass` does not carry field references. Each of
        its `schema` / `dataQuality` / `freshness` entries carries an
        **assertion URN**, and the assertion is what names the column. The link
        is contract -> assertion -> field, and the middle hop is a read.

        Collecting `entry.assertion` and handing it to `references_column` — as
        this did — compares an assertion URN against a schemaField URN and a
        bare field path. It matches neither, so `references_changed_column` was
        False for every contract ever seen, `contract_presence` (12 points, the
        largest single factor) could never fire, and it scored as a measured no,
        so nothing degraded either. The live run showed it plainly: for one
        column, `assertions[0].references_changed_column` true and
        `data_contracts[0].references_changed_column` false.

        Returns `None` when the contract named assertions and none of them could
        be read, which is a gap rather than an answer.
        """
        from datahub.metadata.schema_classes import AssertionInfoClass

        assertion_urns = [
            urn
            for group_name in ("schema", "dataQuality", "freshness")
            for entry in getattr(properties, group_name, None) or []
            if isinstance(urn := getattr(entry, "assertion", None), str) and urn
        ]
        if not assertion_urns:
            # A contract with no resolvable terms governs nothing we can check.
            return None

        found: list[str | None] = []
        resolved = 0
        for assertion_urn in assertion_urns:
            info = self._aspect(assertion_urn, AssertionInfoClass)
            if info is None:
                continue
            resolved += 1
            found.extend(_asserted_fields(info))
        if resolved == 0:
            return None
        return tuple(found)


def _ownership_type_of(entry: Any) -> OwnershipType | None:
    """Map an `Owner.type` / `typeUrn` onto the report's closed set."""
    raw = getattr(entry, "type", None)
    if raw is None and getattr(entry, "typeUrn", None):
        raw = str(entry.typeUrn).rsplit(":", maxsplit=1)[-1]
    normalised = str(raw or "").strip().upper()
    allowed = {
        "TECHNICAL_OWNER",
        "BUSINESS_OWNER",
        "DATA_STEWARD",
        "PRODUCER",
        "CONSUMER",
        "NONE",
    }
    return normalised if normalised in allowed else None  # type: ignore[return-value]


def _asserted_fields(info: Any) -> tuple[str | None, ...]:
    """Collect every field an assertion names, across the assertion sub-types.

    Only structured field references are collected. An assertion's free-text
    description is NOT consulted: `references_changed_column` feeds the report,
    and matching a column name inside prose would put attacker-controlled text
    one step away from a scored factor.
    """
    found: list[str | None] = []

    dataset_assertion = getattr(info, "datasetAssertion", None)
    if dataset_assertion is not None:
        found.extend(getattr(dataset_assertion, "fields", None) or [])

    field_assertion = getattr(info, "fieldAssertion", None)
    if field_assertion is not None:
        for holder_name in ("fieldValuesAssertion", "fieldMetricAssertion"):
            holder = getattr(field_assertion, holder_name, None)
            field = getattr(holder, "field", None)
            if field is not None:
                found.append(getattr(field, "path", None))
                found.append(getattr(field, "urn", None))

    return tuple(found)
