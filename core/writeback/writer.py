"""DataHub mutations: the finding written back into the graph.

Four writes, in the order they matter:

1. a `blast-radius-<level>` tag on the changed dataset, so the finding is
   visible in search and in the UI without opening anything;
2. the `io.blastradius.impactRecord` structured property carrying the
   `WritebackRecord` payload, which is the machine-readable part and the reason
   the next agent does not have to redo this work;
3. a document (`save_document`) with the rendered explanation, for humans;
4. owner assignment on the changed dataset when DataHub has none, because an
   unowned dataset that just broke a dashboard is its own finding.

Only (2) is required. The others are best-effort and degrade individually.
"""

from __future__ import annotations

import json
import re
import time
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from contracts.loader import to_payload
from contracts.models import SeverityLevel, WritebackRecord
from core.datahub.mcp_session import McpSession, ToolCaller
from core.errors import DataHubAccessError
from core.writeback.capabilities import MUTATION_ENV_VAR, WriteCapabilities

if TYPE_CHECKING:  # pragma: no cover - typing only
    from datahub.ingestion.graph.client import DataHubGraph

_MCP = "core.writeback.writer.McpDataHubWriter"
_SDK = "core.writeback.writer.SdkDataHubWriter"

#: Tag URNs are stable and enumerable so they can be provisioned once and
#: filtered on in DataHub search.
TAG_URN_TEMPLATE = "urn:li:tag:blast-radius-{level}"
STRUCTURED_PROPERTY_URN = "urn:li:structuredProperty:io.blastradius.impactRecord"

#: The actor recorded on every audit stamp we emit. A URN rather than a name so
#: DataHub's history shows the writes came from a tool, not from a person whose
#: account happened to hold the token.
ACTOR_URN = "urn:li:corpuser:blast-radius"

#: Delimiters around the documentation block this tool owns. Write-back appends
#: to a dataset's documentation, and a dataset's documentation belongs to the
#: team that wrote it: replacing it wholesale with model-generated prose would
#: destroy human work on every run. Everything between these markers is ours to
#: rewrite; everything outside them is never touched.
DOC_BEGIN = "<!-- blast-radius:begin -->"
DOC_END = "<!-- blast-radius:end -->"

_DOC_BLOCK = re.compile(
    re.escape(DOC_BEGIN) + r".*?" + re.escape(DOC_END),
    re.DOTALL,
)

#: Same bounds, and for the same reason, as `core.datahub.sdk_client`: the SDK
#: retries forever by default, and a write that hangs looks like a broken tool.
_REQUEST_TIMEOUT_SECONDS = 15
_RETRY_MAX_TIMES = 1


def tag_urn_for(level: SeverityLevel) -> str:
    """Return the tag URN for a severity level."""
    return TAG_URN_TEMPLATE.format(level=level)


def render_document_block(title: str, body: str) -> str:
    """Wrap model-generated prose in the delimited, self-labelling block.

    The label lives in the document rather than only in the report because the
    document outlives the pull request: someone reading a dataset's
    documentation in DataHub six months from now has no other way to know which
    paragraphs a language model wrote.
    """
    return (
        f"{DOC_BEGIN}\n"
        f"## {title}\n\n"
        f"{body.strip()}\n\n"
        "_Written by blast-radius. The prose above is model-generated; the "
        "severity score it describes is not._\n"
        f"{DOC_END}"
    )


def merge_document(existing: str | None, block: str) -> str:
    """Insert or replace our block in `existing`, leaving all other text alone.

    Idempotent: re-reviewing a pushed commit rewrites the block in place rather
    than appending a second copy, so a busy pull request does not turn a
    dataset's documentation into a changelog.
    """
    current = (existing or "").strip()
    if not current:
        return block
    if _DOC_BLOCK.search(current):
        return _DOC_BLOCK.sub(lambda _: block, current, count=1)
    return f"{current}\n\n{block}"


def record_property_value(record: WritebackRecord) -> str:
    """Serialise a record for the structured property, schema-validated first.

    `to_payload` is the only supported serialisation, and it validates on the
    way out, so an invalid record fails here rather than becoming malformed
    metadata that the next agent inherits and trusts.
    """
    return json.dumps(to_payload(record), ensure_ascii=False, sort_keys=True)


@runtime_checkable
class DataHubWriter(Protocol):
    """Every mutation blast-radius performs. Separate from the read interface so
    that an analysis can run with a read-only token."""

    @property
    def access_path(self) -> Literal["mcp", "sdk"]:
        """Which path this writer uses."""
        ...

    def add_tag(self, entity_urn: str, tag_urn: str) -> None:
        """Attach a tag to an entity. Idempotent."""
        ...

    def set_structured_property(self, entity_urn: str, record: WritebackRecord) -> None:
        """Write the impact record. The one write that must succeed."""
        ...

    def save_document(self, entity_urn: str, title: str, body: str) -> None:
        """Attach human-readable documentation to an entity."""
        ...

    def assign_owner(self, entity_urn: str, owner_urn: str, ownership_type: str) -> None:
        """Assign an owner to an entity."""
        ...


class McpDataHubWriter:
    """Mutations through mcp-server-datahub. Requires v0.5.0+ and mutations enabled."""

    def __init__(
        self,
        server_command: str,
        gms_url: str,
        token: str | None = None,
        session: ToolCaller | None = None,
    ) -> None:
        self._session = session or McpSession(
            server_command=server_command,
            gms_url=gms_url,
            token=token,
            # The mutation tools are hidden unless the server was started with
            # this set. Passing it to the subprocess we start ourselves means a
            # write path that `doctor` reported as available actually is one.
            extra_env={MUTATION_ENV_VAR: "true"},
        )

    @property
    def access_path(self) -> Literal["mcp", "sdk"]:
        """Report provenance: MCP write path."""
        return "mcp"

    def close(self) -> None:
        """Shut the server subprocess down."""
        self._session.close()

    def add_tag(self, entity_urn: str, tag_urn: str) -> None:
        """Attach a tag through the MCP `add_tags` tool.

        Idempotent by construction: `add_tags` is additive on the server side,
        so re-running a review on a pushed commit does not accumulate
        duplicates. DataHub materialises an unknown tag URN on first use, so no
        separate create step is needed.
        """
        self._session.call("add_tags", {"tag_urns": [tag_urn], "entity_urns": [entity_urn]})

    def set_structured_property(self, entity_urn: str, record: WritebackRecord) -> None:
        """Write the `io.blastradius.impactRecord` structured property.

        The record is serialised through `contracts.loader.to_payload`, which
        validates it against the schema, so an invalid record fails here rather
        than becoming malformed metadata the next agent inherits and trusts.

        `add_structured_properties` sets the value for our property URN and
        leaves every other property alone, which is the overwrite semantics the
        contract asks for: the newest review of a pull request is the true one,
        and `supersedes` carries the history.
        """
        self._session.call(
            "add_structured_properties",
            {
                "property_values": {STRUCTURED_PROPERTY_URN: [record_property_value(record)]},
                "entity_urns": [entity_urn],
            },
        )

    def save_document(self, entity_urn: str, title: str, body: str) -> None:
        """Attach the rendered explanation as a document.

        The body is wrapped by `render_document_block`, so the prose carries its
        own "model-generated" label. The label has to live in the document
        rather than only in the report, because the document outlives the pull
        request.
        """
        self._session.call(
            "save_document",
            {
                "document_type": "Analysis",
                "title": title,
                "content": render_document_block(title, body),
                "urn": entity_urn,
                "related_assets": [entity_urn],
            },
        )

    def assign_owner(self, entity_urn: str, owner_urn: str, ownership_type: str) -> None:
        """Assign an owner. `add_owners` is additive; it never replaces one."""
        self._session.call(
            "add_owners",
            {
                "owner_urns": [owner_urn],
                "entity_urns": [entity_urn],
                "ownership_type": ownership_type,
            },
        )


class SdkDataHubWriter:
    """Mutations through acryl-datahub. The fallback that must always work."""

    def __init__(self, gms_url: str, token: str | None = None) -> None:
        self._gms_url = gms_url
        self._token = token
        self._graph_cache: DataHubGraph | None = None

    @property
    def access_path(self) -> Literal["mcp", "sdk"]:
        """Report provenance: SDK write path."""
        return "sdk"

    # -- connection ----------------------------------------------------------

    @property
    def _graph(self) -> DataHubGraph:
        """One client per writer, built on first use and imported lazily.

        Mirrors `SdkDataHubReader._graph`, including the explicit bounds: the
        SDK's defaults are unbounded, and a write path that hangs in a CI job is
        indistinguishable to a reviewer from one that is broken.
        """
        if self._graph_cache is not None:
            return self._graph_cache
        try:
            from datahub.ingestion.graph.client import DatahubClientConfig, DataHubGraph
        except ImportError as exc:
            msg = (
                "the `datahub` extra is not installed, so the SDK write path is "
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

    def _emit(self, entity_urn: str, aspect: Any) -> None:
        """Emit one aspect, turning any transport failure into DataHubAccessError."""
        from datahub.emitter.mcp import MetadataChangeProposalWrapper

        try:
            self._graph.emit_mcp(MetadataChangeProposalWrapper(entityUrn=entity_urn, aspect=aspect))
        except DataHubAccessError:
            raise
        except Exception as exc:
            msg = f"writing {type(aspect).__name__} to {entity_urn} failed: {exc}"
            raise DataHubAccessError(msg) from exc

    def _audit_stamp(self) -> Any:
        """Build an audit stamp for the current instant.

        The project's rule that the clock is a parameter applies to pure
        functions, whose output must be reproducible. This is the opposite kind
        of code: the stamp records when a mutation actually reached DataHub, and
        only the mutation knows that.
        """
        from datahub.metadata.schema_classes import AuditStampClass

        return AuditStampClass(time=int(time.time() * 1000), actor=ACTOR_URN)

    # -- mutations -----------------------------------------------------------

    def add_tag(self, entity_urn: str, tag_urn: str) -> None:
        """Emit a `globalTags` MCP aspect. Contract: read-modify-write, additive."""
        from datahub.metadata.schema_classes import GlobalTagsClass, TagAssociationClass

        try:
            existing = self._graph.get_aspect(entity_urn, GlobalTagsClass)
        except Exception as exc:
            msg = f"reading globalTags for {entity_urn} failed: {exc}"
            raise DataHubAccessError(msg) from exc

        tags = list(existing.tags) if existing and existing.tags else []
        if any(association.tag == tag_urn for association in tags):
            return  # already present; re-emitting would only churn the audit log
        tags.append(TagAssociationClass(tag=tag_urn))
        self._emit(entity_urn, GlobalTagsClass(tags=tags))

    def set_structured_property(self, entity_urn: str, record: WritebackRecord) -> None:
        """Emit a `structuredProperties` aspect carrying the validated record.

        Our own property is overwritten — the newest review of a pull request is
        the true one, and `supersedes` carries the history — while every other
        property on the entity is preserved.
        """
        from datahub.metadata.schema_classes import (
            StructuredPropertiesClass,
            StructuredPropertyValueAssignmentClass,
        )

        try:
            existing = self._graph.get_aspect(entity_urn, StructuredPropertiesClass)
        except Exception as exc:
            msg = f"reading structuredProperties for {entity_urn} failed: {exc}"
            raise DataHubAccessError(msg) from exc

        others = [
            assignment
            for assignment in (existing.properties if existing and existing.properties else [])
            if assignment.propertyUrn != STRUCTURED_PROPERTY_URN
        ]
        stamp = self._audit_stamp()
        others.append(
            StructuredPropertyValueAssignmentClass(
                propertyUrn=STRUCTURED_PROPERTY_URN,
                values=[record_property_value(record)],
                created=stamp,
                lastModified=stamp,
            )
        )
        self._emit(entity_urn, StructuredPropertiesClass(properties=others))

    def save_document(self, entity_urn: str, title: str, body: str) -> None:
        """Write the explanation into the dataset's documentation, additively.

        `editableDatasetProperties` is the aspect behind DataHub's Documentation
        tab. It is read-modify-written through `merge_document` so that a team's
        own documentation survives, and so that a second run over the same pull
        request replaces our block instead of appending another.
        """
        from datahub.metadata.schema_classes import EditableDatasetPropertiesClass

        try:
            existing = self._graph.get_aspect(entity_urn, EditableDatasetPropertiesClass)
        except Exception as exc:
            msg = f"reading editableDatasetProperties for {entity_urn} failed: {exc}"
            raise DataHubAccessError(msg) from exc

        merged = merge_document(
            existing.description if existing else None,
            render_document_block(title, body),
        )
        if existing is not None and existing.description == merged:
            return
        self._emit(
            entity_urn,
            EditableDatasetPropertiesClass(
                description=merged,
                created=existing.created if existing else self._audit_stamp(),
                lastModified=self._audit_stamp(),
                name=existing.name if existing else None,
            ),
        )

    def assign_owner(self, entity_urn: str, owner_urn: str, ownership_type: str) -> None:
        """Emit an additive `ownership` aspect. Never replaces an existing owner."""
        from datahub.metadata.schema_classes import OwnerClass, OwnershipClass

        try:
            existing = self._graph.get_aspect(entity_urn, OwnershipClass)
        except Exception as exc:
            msg = f"reading ownership for {entity_urn} failed: {exc}"
            raise DataHubAccessError(msg) from exc

        owners = list(existing.owners) if existing and existing.owners else []
        if any(owner.owner == owner_urn for owner in owners):
            return
        owners.append(OwnerClass(owner=owner_urn, type=ownership_type))
        self._emit(entity_urn, OwnershipClass(owners=owners))


def build_writer(
    capabilities: WriteCapabilities,
    gms_url: str,
    mcp_server_command: str,
    token: str | None = None,
) -> DataHubWriter | None:
    """Pick a write path, or None when there is none.

    Real implementation: choosing between two configured paths is a decision the
    scaffold can already make correctly, and `doctor` needs it to be able to
    explain itself. Returning None rather than raising lets a caller finish the
    review and report a `datahub_writeback` degradation, which is the right
    behaviour for a PR comment that is otherwise complete.
    """
    path = capabilities.preferred_path
    if path == "mcp":
        return McpDataHubWriter(server_command=mcp_server_command, gms_url=gms_url, token=token)
    if path == "sdk":
        return SdkDataHubWriter(gms_url=gms_url, token=token)
    return None
