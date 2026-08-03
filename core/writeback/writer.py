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

from typing import Literal, Protocol, runtime_checkable

from contracts.models import SeverityLevel, WritebackRecord
from core.errors import OWNER_A, StubNotImplementedError
from core.writeback.capabilities import WriteCapabilities

_MCP = "core.writeback.writer.McpDataHubWriter"
_SDK = "core.writeback.writer.SdkDataHubWriter"

#: Tag URNs are stable and enumerable so they can be provisioned once and
#: filtered on in DataHub search.
TAG_URN_TEMPLATE = "urn:li:tag:blast-radius-{level}"
STRUCTURED_PROPERTY_URN = "urn:li:structuredProperty:io.blastradius.impactRecord"


def tag_urn_for(level: SeverityLevel) -> str:
    """Return the tag URN for a severity level."""
    return TAG_URN_TEMPLATE.format(level=level)


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

    def __init__(self, server_command: str, gms_url: str, token: str | None = None) -> None:
        self._server_command = server_command
        self._gms_url = gms_url
        self._token = token

    @property
    def access_path(self) -> Literal["mcp", "sdk"]:
        """Report provenance: MCP write path."""
        return "mcp"

    def add_tag(self, entity_urn: str, tag_urn: str) -> None:
        """Attach a tag through the MCP mutation tool.

        Contract: idempotent — re-running a review on a pushed commit must not
        accumulate tags. Create the tag entity if DataHub does not know it yet.
        """
        raise StubNotImplementedError(
            f"{_MCP}.add_tag",
            OWNER_A,
            "idempotent tag mutation via MCP, creating the tag if absent",
        )

    def set_structured_property(self, entity_urn: str, record: WritebackRecord) -> None:
        """Write the `io.blastradius.impactRecord` structured property.

        Contract: serialise `record` through `contracts.loader.to_payload` so it
        is schema-validated before it leaves the process. Ensure the structured
        property definition exists (create on first use). Overwrite rather than
        append: the newest review of a PR is the true one, and `supersedes`
        carries the history.
        """
        raise StubNotImplementedError(
            f"{_MCP}.set_structured_property",
            OWNER_A,
            "validated WritebackRecord into the io.blastradius.impactRecord property via MCP",
        )

    def save_document(self, entity_urn: str, title: str, body: str) -> None:
        """Attach the rendered explanation as documentation.

        Contract: the body contains model-generated prose and must be labelled
        as such in the document itself, not only in the report.
        """
        raise StubNotImplementedError(
            f"{_MCP}.save_document", OWNER_A, "MCP save_document with model-generated labelling"
        )

    def assign_owner(self, entity_urn: str, owner_urn: str, ownership_type: str) -> None:
        """Assign an owner. Contract: never replace an existing owner, only add."""
        raise StubNotImplementedError(
            f"{_MCP}.assign_owner", OWNER_A, "additive ownership mutation via MCP"
        )


class SdkDataHubWriter:
    """Mutations through acryl-datahub. The fallback that must always work."""

    def __init__(self, gms_url: str, token: str | None = None) -> None:
        self._gms_url = gms_url
        self._token = token

    @property
    def access_path(self) -> Literal["mcp", "sdk"]:
        """Report provenance: SDK write path."""
        return "sdk"

    def add_tag(self, entity_urn: str, tag_urn: str) -> None:
        """Emit a `globalTags` MCP aspect. Contract: read-modify-write, additive."""
        raise StubNotImplementedError(
            f"{_SDK}.add_tag", OWNER_A, "globalTags aspect emit, read-modify-write, additive"
        )

    def set_structured_property(self, entity_urn: str, record: WritebackRecord) -> None:
        """Emit a `structuredProperties` aspect carrying the validated record."""
        raise StubNotImplementedError(
            f"{_SDK}.set_structured_property",
            OWNER_A,
            "structuredProperties aspect emit with a schema-validated payload",
        )

    def save_document(self, entity_urn: str, title: str, body: str) -> None:
        """Emit an `institutionalMemory` or documentation aspect."""
        raise StubNotImplementedError(
            f"{_SDK}.save_document", OWNER_A, "documentation aspect emit via the SDK"
        )

    def assign_owner(self, entity_urn: str, owner_urn: str, ownership_type: str) -> None:
        """Emit an additive `ownership` aspect."""
        raise StubNotImplementedError(
            f"{_SDK}.assign_owner", OWNER_A, "additive ownership aspect emit via the SDK"
        )


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
