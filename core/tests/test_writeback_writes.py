"""The mutations themselves: what they send, and what they refuse to destroy.

Write-back is the only part of blast-radius that changes someone else's data.
Every test here is about a way that could go wrong quietly — clobbering a team's
documentation, replacing an owner, accumulating duplicates on every push, or
sending an unvalidated payload into the catalog for the next agent to trust.
"""

from __future__ import annotations

from typing import Any

import pytest

from contracts.loader import load_impact_report, to_payload
from contracts.models import WritebackRecord
from core.errors import DataHubAccessError
from core.tests.test_mcp_client import FakeSession
from core.writeback.record import build_record
from core.writeback.writer import (
    DOC_BEGIN,
    DOC_END,
    STRUCTURED_PROPERTY_URN,
    McpDataHubWriter,
    SdkDataHubWriter,
    merge_document,
    record_property_value,
    render_document_block,
    tag_urn_for,
)

DATASET = "urn:li:dataset:(urn:li:dataPlatform:dbt,blast_radius_demo.main.dim_customers,PROD)"


@pytest.fixture
def record() -> WritebackRecord:
    from contracts.loader import iter_fixture_dirs

    report = load_impact_report(iter_fixture_dirs()[0] / "expected_impact_report.json")
    return build_record(report, detected_at="2026-08-07T12:00:00Z")


# -- documentation is never destroyed ----------------------------------------


def test_existing_documentation_survives() -> None:
    """A dataset's documentation belongs to the team that wrote it."""
    human = "# dim_customers\n\nOne row per customer. Owned by the CRM squad."

    merged = merge_document(human, render_document_block("Impact", "prose"))

    assert human in merged
    assert DOC_BEGIN in merged and DOC_END in merged


def test_rewriting_replaces_our_block_instead_of_appending() -> None:
    """Otherwise a busy pull request turns documentation into a changelog."""
    first = merge_document("Human docs.", render_document_block("Impact", "first"))
    second = merge_document(first, render_document_block("Impact", "second"))

    assert second.count(DOC_BEGIN) == 1
    assert "second" in second
    assert "first" not in second
    assert "Human docs." in second


def test_merging_is_idempotent() -> None:
    block = render_document_block("Impact", "prose")
    once = merge_document("Human docs.", block)

    assert merge_document(once, block) == once


def test_the_block_labels_itself_as_model_generated() -> None:
    """The document outlives the pull request, so the label must travel with it."""
    assert "model-generated" in render_document_block("Impact", "prose")


# -- the record is validated before it leaves the process --------------------


def test_the_property_value_is_schema_validated(record: WritebackRecord) -> None:
    """Malformed metadata is worse than none: the next agent trusts it."""
    import json

    assert json.loads(record_property_value(record)) == to_payload(record)


# -- the SDK writer: read-modify-write, never replace -------------------------


class FakeGraph:
    """A DataHubGraph stand-in that records emitted aspects."""

    def __init__(self, aspects: dict[str, Any] | None = None) -> None:
        self.aspects = aspects or {}
        self.emitted: list[Any] = []

    def get_aspect(self, urn: str, aspect_type: type) -> Any:
        return self.aspects.get(aspect_type.__name__)

    def emit_mcp(self, mcp: Any) -> None:
        self.emitted.append(mcp.aspect)


def sdk_writer(graph: FakeGraph) -> SdkDataHubWriter:
    writer = SdkDataHubWriter("http://gms")
    writer._graph_cache = graph  # type: ignore[assignment]
    return writer


def test_adding_a_tag_keeps_the_tags_already_there() -> None:
    from datahub.metadata.schema_classes import GlobalTagsClass, TagAssociationClass

    graph = FakeGraph(
        {"GlobalTagsClass": GlobalTagsClass(tags=[TagAssociationClass("urn:li:tag:pii")])}
    )

    sdk_writer(graph).add_tag(DATASET, tag_urn_for("critical"))

    assert [association.tag for association in graph.emitted[0].tags] == [
        "urn:li:tag:pii",
        "urn:li:tag:blast-radius-critical",
    ]


def test_adding_a_tag_twice_emits_nothing_the_second_time() -> None:
    """Re-running a review on a pushed commit must not churn the audit log."""
    from datahub.metadata.schema_classes import GlobalTagsClass, TagAssociationClass

    graph = FakeGraph(
        {"GlobalTagsClass": GlobalTagsClass(tags=[TagAssociationClass(tag_urn_for("critical"))])}
    )

    sdk_writer(graph).add_tag(DATASET, tag_urn_for("critical"))

    assert graph.emitted == []


def test_assigning_an_owner_never_replaces_one() -> None:
    """Replacing an owner reassigns responsibility for a dataset silently."""
    from datahub.metadata.schema_classes import OwnerClass, OwnershipClass

    graph = FakeGraph(
        {
            "OwnershipClass": OwnershipClass(
                owners=[OwnerClass(owner="urn:li:corpuser:dana.eng", type="TECHNICAL_OWNER")]
            )
        }
    )

    sdk_writer(graph).assign_owner(DATASET, "urn:li:corpuser:sam.data", "TECHNICAL_OWNER")

    assert [owner.owner for owner in graph.emitted[0].owners] == [
        "urn:li:corpuser:dana.eng",
        "urn:li:corpuser:sam.data",
    ]


def test_the_structured_property_leaves_other_properties_alone(
    record: WritebackRecord,
) -> None:
    """We own one property on the entity, not the whole aspect."""
    from datahub.metadata.schema_classes import (
        StructuredPropertiesClass,
        StructuredPropertyValueAssignmentClass,
    )

    other = StructuredPropertyValueAssignmentClass(
        propertyUrn="urn:li:structuredProperty:io.acme.tier", values=["gold"]
    )
    graph = FakeGraph({"StructuredPropertiesClass": StructuredPropertiesClass(properties=[other])})

    sdk_writer(graph).set_structured_property(DATASET, record)

    written = {p.propertyUrn for p in graph.emitted[0].properties}
    assert written == {"urn:li:structuredProperty:io.acme.tier", STRUCTURED_PROPERTY_URN}


def test_rewriting_the_property_does_not_accumulate_copies(
    record: WritebackRecord,
) -> None:
    """The newest review of a pull request is the true one."""
    from datahub.metadata.schema_classes import (
        StructuredPropertiesClass,
        StructuredPropertyValueAssignmentClass,
    )

    stale = StructuredPropertyValueAssignmentClass(
        propertyUrn=STRUCTURED_PROPERTY_URN, values=["{}"]
    )
    graph = FakeGraph({"StructuredPropertiesClass": StructuredPropertiesClass(properties=[stale])})

    sdk_writer(graph).set_structured_property(DATASET, record)

    ours = [p for p in graph.emitted[0].properties if p.propertyUrn == STRUCTURED_PROPERTY_URN]
    assert len(ours) == 1
    assert ours[0].values == [record_property_value(record)]


def test_a_transport_failure_becomes_a_blast_radius_error(record: WritebackRecord) -> None:
    """A caller degrades on our error type; it should not see the SDK's."""

    class ExplodingGraph(FakeGraph):
        def emit_mcp(self, mcp: Any) -> None:
            raise RuntimeError("connection reset")

    with pytest.raises(DataHubAccessError, match="connection reset"):
        sdk_writer(ExplodingGraph()).set_structured_property(DATASET, record)


# -- the MCP writer: the verified tool names ----------------------------------


def mcp_writer() -> tuple[McpDataHubWriter, FakeSession]:
    session = FakeSession()
    return McpDataHubWriter("mcp-server-datahub", "http://gms", session=session), session


def test_the_mcp_writer_calls_the_tools_the_server_actually_registers(
    record: WritebackRecord,
) -> None:
    """Tool names verified against mcp-server-datahub 0.6.0's registered list."""
    writer, session = mcp_writer()

    writer.add_tag(DATASET, tag_urn_for("critical"))
    writer.set_structured_property(DATASET, record)
    writer.save_document(DATASET, "Impact", "prose")
    writer.assign_owner(DATASET, "urn:li:corpuser:sam.data", "TECHNICAL_OWNER")

    assert [tool for tool, _ in session.calls] == [
        "add_tags",
        "add_structured_properties",
        "save_document",
        "add_owners",
    ]


def test_the_mcp_property_payload_is_keyed_by_property_urn(
    record: WritebackRecord,
) -> None:
    writer, session = mcp_writer()

    writer.set_structured_property(DATASET, record)

    _, arguments = session.calls[0]
    assert arguments["property_values"] == {
        STRUCTURED_PROPERTY_URN: [record_property_value(record)]
    }
    assert arguments["entity_urns"] == [DATASET]


def test_the_mcp_document_carries_the_model_generated_label() -> None:
    writer, session = mcp_writer()

    writer.save_document(DATASET, "Impact", "prose")

    _, arguments = session.calls[0]
    assert "model-generated" in arguments["content"]
    assert arguments["document_type"] == "Analysis"
