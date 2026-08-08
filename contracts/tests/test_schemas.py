"""The contracts are the interface. These tests are what keeps them frozen.

Owned by BOTH owners. A change that makes one of these fail is an interface
change and needs review from both.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from contracts.loader import (
    SCHEMA_FILES,
    iter_fixture_dirs,
    load_change_set,
    load_impact_report,
    load_schema,
    to_payload,
    validate_instance,
)

pytestmark = pytest.mark.contract

#: The one place in change_set.schema.json where unconstrained free text is
#: allowed. Everything else must be an identifier with a pattern, an enum, a
#: const or a format. See test_only_untrusted_text_is_free_text.
FREE_TEXT_ALLOWLIST = {
    "$defs.untrustedText.properties.value",
}

CONSTRAINING_KEYWORDS = ("pattern", "enum", "const", "format")


@pytest.mark.parametrize("name", sorted(SCHEMA_FILES))
def test_schema_is_valid_draft_2020_12(name: str) -> None:
    Draft202012Validator.check_schema(load_schema(name))


@pytest.mark.parametrize("name", sorted(SCHEMA_FILES))
def test_schema_has_identity_and_description(name: str) -> None:
    schema = load_schema(name)
    assert schema["$id"], f"{name} needs a stable $id"
    assert schema["title"], f"{name} needs a title"
    assert len(schema["description"]) > 80, (
        f"{name} needs a description that says who produces it and who consumes it"
    )


@pytest.mark.parametrize("name", sorted(SCHEMA_FILES))
def test_schema_is_closed(name: str) -> None:
    """Every object must forbid unknown properties.

    An open object is how a typo in one owner's code becomes a silently missing
    field in the other owner's renderer.
    """
    open_objects: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "additionalProperties" not in node:
                open_objects.append(path or "<root>")
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else key)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(load_schema(name), "")
    assert not open_objects, f"{name} has open objects: {open_objects}"


def test_only_untrusted_text_is_free_text() -> None:
    """No unconstrained string may exist outside an untrusted-text envelope.

    This is the schema-level expression of the project's central claim. If a
    plain free-text field appears anywhere else in a ChangeSet, then prose has
    a path into the engine that does not go through core/untrusted, and the
    architectural guarantee is gone.
    """
    offenders: list[str] = []

    def is_string_typed(node: dict[str, Any]) -> bool:
        declared = node.get("type")
        return declared == "string" or (isinstance(declared, list) and "string" in declared)

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            unconstrained = is_string_typed(node) and not any(
                k in node for k in CONSTRAINING_KEYWORDS
            )
            if unconstrained and path not in FREE_TEXT_ALLOWLIST:
                offenders.append(path)
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else key)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(load_schema("change_set"), "")
    assert not offenders, (
        f"unconstrained free-text fields outside the untrusted envelope: {offenders}"
    )


def test_severity_computed_by_is_a_constant() -> None:
    """A report cannot claim its severity came from anywhere but deterministic code."""
    schema = load_schema("impact_report")
    assert schema["$defs"]["severity"]["properties"]["computed_by"]["const"] == "deterministic"


def test_untrusted_finding_effect_is_a_constant() -> None:
    """A detection can never be recorded as having changed a score."""
    finding = load_schema("impact_report")["$defs"]["untrustedFinding"]
    assert finding["properties"]["effect_on_severity"]["const"] == "none"
    assert finding["properties"]["is_heuristic"]["const"] is True


def test_explanation_is_marked_model_generated() -> None:
    """Model prose is always labelled, and the label is not the model's to write."""
    explanation = load_schema("impact_report")["$defs"]["explanation"]
    assert explanation["properties"]["generated_by"]["const"] == "llm"
    assert explanation["properties"]["is_model_generated"]["const"] is True
    assert "did not gate any write" in explanation["properties"]["disclaimer"]["const"].lower()


def test_severity_requires_the_full_factor_breakdown() -> None:
    severity = load_schema("impact_report")["$defs"]["severity"]
    assert severity["properties"]["factors"]["minItems"] == 7
    assert severity["properties"]["factors"]["maxItems"] == 7


# ---------------------------------------------------------------------------
# Golden fixtures
# ---------------------------------------------------------------------------

FIXTURE_DIRS = iter_fixture_dirs()
REQUIRED_FIXTURES = {"01_rename", "02_removal_contract", "03_adversarial_description"}


def test_required_fixtures_exist() -> None:
    names = {d.name for d in FIXTURE_DIRS}
    assert names >= REQUIRED_FIXTURES, f"missing golden fixtures: {REQUIRED_FIXTURES - names}"


@pytest.mark.parametrize("directory", FIXTURE_DIRS, ids=lambda d: d.name)
def test_fixture_is_a_complete_pair(directory: Any) -> None:
    assert (directory / "change_set.json").is_file()
    assert (directory / "expected_impact_report.json").is_file()


@pytest.mark.parametrize("directory", FIXTURE_DIRS, ids=lambda d: d.name)
def test_fixture_change_sets_validate(directory: Any) -> None:
    for path in sorted(directory.glob("change_set*.json")):
        load_change_set(path)


@pytest.mark.parametrize("directory", FIXTURE_DIRS, ids=lambda d: d.name)
def test_fixture_reports_validate(directory: Any) -> None:
    load_impact_report(directory / "expected_impact_report.json")


@pytest.mark.parametrize("directory", FIXTURE_DIRS, ids=lambda d: d.name)
def test_fixture_round_trips_through_the_models(directory: Any) -> None:
    """Schema -> model -> schema must be lossless for the fields we rely on.

    A round trip that silently drops a field is how the renderer ends up with
    an empty section and nobody notices until the demo.
    """
    for path in sorted(directory.glob("change_set*.json")):
        model = load_change_set(path)
        validate_instance(to_payload(model), "change_set", path)

    report_path = directory / "expected_impact_report.json"
    report = load_impact_report(report_path)
    validate_instance(to_payload(report), "impact_report", report_path)


def _required_nullable_properties(name: str) -> list[str]:
    """Every property a schema both requires and allows to be null."""
    schema = load_schema(name)
    found: list[str] = []

    def permits_null(prop: dict[str, Any]) -> bool:
        declared = prop.get("type")
        if isinstance(declared, list) and "null" in declared:
            return True
        return any(
            isinstance(option, dict) and option.get("type") == "null"
            for option in prop.get("anyOf", []) or []
        )

    def walk(node: Any, path: str) -> None:
        if not isinstance(node, dict):
            return
        properties = node.get("properties", {}) or {}
        for key in node.get("required", []) or []:
            prop = properties.get(key)
            if isinstance(prop, dict) and permits_null(prop):
                found.append(f"{path}.{key}")
        for container in ("properties", "$defs"):
            for key, value in (node.get(container) or {}).items():
                walk(value, f"{path}/{key}" if container == "$defs" else path)
        for key in ("items", "additionalProperties"):
            walk(node.get(key), path)
        for key in ("anyOf", "oneOf", "allOf"):
            for value in node.get(key) or []:
                walk(value, path)

    walk(schema, name)
    return found


def _with_first_factor_field(value: Any, field: str) -> Any:
    """Return fixture 0's report with one severity factor field overridden.

    Rebuilt with `model_copy` rather than mutated: the contract models are
    frozen, which is the reason a caller cannot quietly edit a validated report.
    """
    report = load_impact_report(FIXTURE_DIRS[0] / "expected_impact_report.json")
    impact = report.column_impacts[0]
    factors = list(impact.severity.factors)
    factors[0] = factors[0].model_copy(update={field: value})
    severity = impact.severity.model_copy(update={"factors": tuple(factors)})
    return report.model_copy(
        update={
            "column_impacts": (
                impact.model_copy(update={"severity": severity}),
                *report.column_impacts[1:],
            )
        }
    )


def test_a_required_nullable_field_survives_serialisation() -> None:
    """A required field must keep its key when null, or the payload self-invalidates.

    `severityFactor.raw_value` is required and nullable, and is genuinely null
    twice in ordinary operation: `hop_proximity` when nothing is downstream, and
    `query_usage` when usage was never ingested. Serialising with a blanket
    `exclude_none` dropped the key, so `core` emitted reports that
    `load_impact_report` then refused to read.
    """
    assert _required_nullable_properties("impact_report") == [
        "impact_report/severityFactor.raw_value"
    ], "a new required-and-nullable property was added; extend this test's coverage"

    payload = to_payload(_with_first_factor_field(None, "raw_value"))

    serialised = payload["column_impacts"][0]["severity"]["factors"][0]
    assert "raw_value" in serialised, "required key dropped because its value was null"
    assert serialised["raw_value"] is None
    validate_instance(payload, "impact_report", FIXTURE_DIRS[0])


def test_an_optional_null_is_still_dropped() -> None:
    """The pruning must stay selective, or optional nulls fail their non-null types."""
    payload = to_payload(_with_first_factor_field(None, "description"))

    serialised = payload["column_impacts"][0]["severity"]["factors"][0]

    assert "description" not in serialised


@pytest.mark.parametrize("directory", FIXTURE_DIRS, ids=lambda d: d.name)
def test_fixture_report_is_pinned_to_its_change_set(directory: Any) -> None:
    """The report's digest must match the change set it claims to describe."""
    from contracts.loader import change_set_digest

    change_set = load_change_set(directory / "change_set.json")
    report = load_impact_report(directory / "expected_impact_report.json")
    assert report.change_set_ref.change_set_sha256 == change_set_digest(change_set)


@pytest.mark.parametrize("directory", FIXTURE_DIRS, ids=lambda d: d.name)
def test_fixture_untrusted_ids_are_content_addressed(directory: Any) -> None:
    """Ids must be derivable from the text, or the prompt delimiter is forgeable."""
    from contracts.canonical import untrusted_id

    for path in sorted(directory.glob("change_set*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        texts = list(raw.get("untrusted_text", []))
        for change in raw["column_changes"]:
            texts.extend(change.get("untrusted_text", []))
        for text in texts:
            assert text["id"] == untrusted_id(text["value"]), (
                f"{path.name}: id {text['id']} is not the hash of its content"
            )
