"""The parts of write-back and validation that are already real."""

from __future__ import annotations

from pathlib import Path

import pytest

from contracts.loader import fixture_dir, load_impact_report, to_payload, validate_instance
from core.validate.dbt import MAX_ATTEMPTS, truncate_output, validate_with_retry
from core.writeback.capabilities import WriteCapabilities
from core.writeback.record import build_record
from core.writeback.writer import (
    McpDataHubWriter,
    SdkDataHubWriter,
    build_writer,
    tag_urn_for,
)

DETECTED_AT = "2026-03-18T16:45:00Z"


def capabilities(**overrides: object) -> WriteCapabilities:
    base = {
        "mcp_available": False,
        "mcp_version": None,
        "mcp_mutations_enabled": False,
        "proposals_available": False,
        "sdk_available": False,
        "gms_reachable": True,
    }
    return WriteCapabilities(**{**base, **overrides})  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Write-back record
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["01_rename", "02_removal_contract", "03_adversarial_description"])
def test_record_built_from_a_report_satisfies_its_schema(name: str) -> None:
    report = load_impact_report(fixture_dir(name) / "expected_impact_report.json")
    record = build_record(report, detected_at=DETECTED_AT)
    validate_instance(to_payload(record), "writeback_record")


def test_record_copies_severity_rather_than_recomputing_it() -> None:
    """The number in DataHub and the number in the PR comment cannot disagree."""
    report = load_impact_report(fixture_dir("02_removal_contract") / "expected_impact_report.json")
    record = build_record(report, detected_at=DETECTED_AT)
    assert record.severity.score == report.overall_severity.score
    assert record.severity.level == report.overall_severity.level
    assert record.severity.rule_version == report.overall_severity.rule_version


def test_record_deduplicates_and_sorts_downstream_urns() -> None:
    """Two runs over the same PR must produce the same bytes."""
    report = load_impact_report(fixture_dir("02_removal_contract") / "expected_impact_report.json")
    record = build_record(report, detected_at=DETECTED_AT)
    assert list(record.downstream_urns) == sorted(set(record.downstream_urns))
    assert record.downstream_entity_count == len(record.downstream_urns)


def test_record_is_machine_parseable_not_prose() -> None:
    """The consumer is the next agent, so nothing important may be free text."""
    report = load_impact_report(
        fixture_dir("03_adversarial_description") / "expected_impact_report.json"
    )
    record = build_record(report, detected_at=DETECTED_AT)
    assert record.status == "detected"
    assert record.changed_columns[0].change_kind == "removed"
    assert record.changed_columns[0].severity_level == "critical"
    assert record.structured_property_urn.endswith("io.blastradius.impactRecord")


def test_tag_urns_are_stable_per_level() -> None:
    assert tag_urn_for("critical") == "urn:li:tag:blast-radius-critical"


# --------------------------------------------------------------------------
# Write path selection and graceful degradation
# --------------------------------------------------------------------------


def test_mcp_is_preferred_when_mutations_are_enabled() -> None:
    caps = capabilities(mcp_available=True, mcp_version="0.5.1", mcp_mutations_enabled=True)
    assert caps.preferred_path == "mcp"
    assert isinstance(build_writer(caps, "http://gms", "mcp-server-datahub"), McpDataHubWriter)


def test_sdk_is_the_fallback_when_mutations_are_disabled() -> None:
    """The documented degradation: MCP present but mutations off."""
    caps = capabilities(mcp_available=True, mcp_version="0.5.1", sdk_available=True)
    assert caps.preferred_path == "sdk"
    assert isinstance(build_writer(caps, "http://gms", "mcp-server-datahub"), SdkDataHubWriter)
    assert "TOOLS_IS_MUTATION_ENABLED" in caps.explain()


def test_no_writer_when_no_path_is_available() -> None:
    """None rather than an exception: the review is still worth posting."""
    caps = capabilities()
    assert caps.can_write is False
    assert build_writer(caps, "http://gms", "mcp-server-datahub") is None
    assert "No write path" in caps.explain()


# --------------------------------------------------------------------------
# Validation retry loop
# --------------------------------------------------------------------------


def test_retry_budget_is_bounded() -> None:
    assert MAX_ATTEMPTS == 3


def test_retry_rejects_a_nonsensical_budget() -> None:
    """Checked before anything is compiled, so the error is about the caller."""
    with pytest.raises(ValueError, match="at least 1"):
        validate_with_retry(
            Path(), "model", Path("model.sql"), "select 1", lambda _: "select 1", max_attempts=0
        )


def test_truncate_keeps_the_tail_where_dbt_puts_the_error() -> None:
    output = "noise\n" * 400 + "Compilation Error in model x"
    truncated = truncate_output(output, limit=120)
    assert truncated.endswith("Compilation Error in model x")
    assert len(truncated) <= 120
    assert truncated.startswith("…")


def test_short_output_is_left_alone() -> None:
    assert truncate_output("fine") == "fine"
