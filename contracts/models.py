"""Typed models mirroring the JSON Schemas in this directory.

Every crossing of a module boundary in blast-radius goes through one of these
models, and every model is validated against its JSON Schema before it is
written and after it is read (`contracts/loader.py`). The schema is the
authority; these models exist so that Python code gets types, and so that a
handful of invariants the schema cannot express (content-addressed ids,
factor uniqueness) are enforced in one place for both owners.

Do not add a field here without adding it to the corresponding schema in the
same commit. `contracts/tests/test_model_schema_parity.py` fails if you do.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from contracts.canonical import untrusted_id

# --------------------------------------------------------------------------
# Shared vocabularies. These are closed sets on purpose: a new value is an
# interface change and must be agreed by both owners.
# --------------------------------------------------------------------------
ChangeKind = Literal["removed", "renamed", "type_changed", "added"]
SeverityLevel = Literal["critical", "high", "medium", "low"]
UntrustedSource = Literal[
    "pull_request_title",
    "pull_request_body",
    "pull_request_review_comment",
    "dbt_yaml_description",
    "dbt_yaml_meta",
    "dbt_docs_block",
    "sql_comment",
]
EntityType = Literal[
    "dataset",
    "dashboard",
    "chart",
    "mlFeature",
    "mlModel",
    "mlPrimaryKey",
    "dataJob",
    "dataFlow",
    "notebook",
]
TransformationType = Literal[
    "identity", "rename", "cast", "expression", "aggregation", "join", "filter", "unknown"
]
OwnerType = Literal["corpuser", "corpGroup"]
OwnershipType = Literal[
    "TECHNICAL_OWNER", "BUSINESS_OWNER", "DATA_STEWARD", "PRODUCER", "CONSUMER", "NONE"
]
OwnerSource = Literal["changed_dataset", "downstream_entity", "container_inherited"]
AssertionType = Literal["FRESHNESS", "VOLUME", "FIELD", "SQL", "DATASET", "SCHEMA", "CUSTOM"]
AssertionResult = Literal["SUCCESS", "FAILURE", "ERROR", "INIT", "UNKNOWN"]
ContractState = Literal["ACTIVE", "PENDING"]
UsageSource = Literal["datahub_usage", "datahub_queries", "unavailable"]
#: "mcp+sdk" is a composed run: MCP served every read it exposes, and the SDK
#: served the two it does not. `mcp-server-datahub` has no data-contract tool at
#: all, and its assertion tool is declared `min_version(cloud=...)` with no OSS
#: minimum, so on an open-source DataHub neither `contract_presence` nor
#: `assertion_presence` is answerable over MCP alone. Reporting "mcp" for such a
#: run would misattribute two of the seven severity factors.
AccessPath = Literal["mcp", "sdk", "mcp+sdk"]
FixLanguage = Literal["sql", "yaml", "python", "markdown"]
WritebackStatus = Literal[
    "detected", "acknowledged", "fix_proposed", "resolved", "dismissed", "expired"
]
Capability = Literal[
    "column_level_lineage",
    "query_usage",
    "assertions",
    "data_contracts",
    "ownership",
    "llm_explanation",
    "fix_generation",
    "fix_validation",
    "datahub_writeback",
]
FactorName = Literal[
    "change_kind_risk",
    "downstream_reach",
    "hop_proximity",
    "query_usage",
    "contract_presence",
    "assertion_presence",
    "critical_consumer",
]

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ChangeId = Annotated[str, Field(pattern=r"^cc-[0-9]{1,4}$")]
FixId = Annotated[str, Field(pattern=r"^fix-[0-9]{1,4}$")]
UntrustedId = Annotated[str, Field(pattern=r"^ut-[0-9a-f]{12}$")]
RuleVersion = Annotated[str, Field(pattern=r"^sev-v[0-9]+$")]
Urn = Annotated[str, Field(pattern=r"^urn:li:", max_length=512)]
RawFactorValue = bool | int | float | str | None


class _Model(BaseModel):
    """Base for every contract model: immutable and closed to unknown fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


# ==========================================================================
# ChangeSet  --  produced by OWNER B (ci/diff), consumed by OWNER A (core/)
# ==========================================================================


class UntrustedText(_Model):
    """Free text that originated outside blast-radius, preserved verbatim.

    It may contain instructions aimed at the review agent. Nothing here is
    sanitised: quarantine is the reader's job, not the extractor's, so that the
    agent and a human reviewer are looking at exactly the same bytes.
    """

    id: UntrustedId
    field: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.\[\]/-]+$", max_length=256)]
    source: UntrustedSource
    value: Annotated[str, Field(max_length=20000)]
    file_path: str | None = None
    line: int | None = None

    @model_validator(mode="after")
    def _id_must_be_content_addressed(self) -> UntrustedText:
        expected = untrusted_id(self.value)
        if self.id != expected:
            msg = (
                f"untrusted_text.id {self.id!r} does not match its content; "
                f"expected {expected!r}. The id is the delimiter nonce and must be "
                "derived from the value, never assigned."
            )
            raise ValueError(msg)
        return self


class PullRequestRef(_Model):
    """Identity of the reviewed pull request. Deliberately holds no free text."""

    number: Annotated[int, Field(ge=1)]
    repo: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", max_length=128)]
    head_sha: Annotated[str, Field(pattern=r"^[0-9a-f]{7,40}$")]
    base_sha: Annotated[str, Field(pattern=r"^[0-9a-f]{7,40}$")]
    url: str | None = None
    author: str | None = None


class Extractor(_Model):
    """Provenance of the extraction. `method` never names a model: parsing is deterministic."""

    name: Annotated[str, Field(pattern=r"^[a-z0-9-]+$", max_length=64)]
    version: Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]
    method: Literal["sqlglot", "dbt_manifest", "hybrid"] | None = None


class ColumnChange(_Model):
    """One (dataset, column) affected by the diff."""

    id: ChangeId
    dbt_model: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=128)]
    dataset_urn: Annotated[str, Field(pattern=r"^urn:li:dataset:\(", max_length=512)]
    dataset_name: Annotated[
        str, Field(pattern=r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$", max_length=256)
    ]
    column: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=128)]
    change_kind: ChangeKind
    old_value: str | None = None
    new_value: str | None = None
    file_path: Annotated[str, Field(pattern=r"^[^\n\r]+$", max_length=1024)]
    line: int | None = None
    untrusted_text: tuple[UntrustedText, ...] = ()

    @model_validator(mode="after")
    def _values_required_by_kind(self) -> ColumnChange:
        required: dict[ChangeKind, tuple[str, ...]] = {
            "renamed": ("old_value", "new_value"),
            "type_changed": ("old_value", "new_value"),
            "removed": ("old_value",),
            "added": ("new_value",),
        }
        missing = [f for f in required[self.change_kind] if getattr(self, f) is None]
        if missing:
            msg = f"change_kind={self.change_kind!r} requires {', '.join(missing)}"
            raise ValueError(msg)
        return self


class ChangeSet(_Model):
    """A parsed data pull-request diff. The input to the whole pipeline."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    pull_request: PullRequestRef
    column_changes: Annotated[tuple[ColumnChange, ...], Field(min_length=1)]
    untrusted_text: tuple[UntrustedText, ...] = ()
    extracted_at: str | None = None
    extractor: Extractor | None = None

    def all_untrusted_text(self) -> tuple[UntrustedText, ...]:
        """Every untrusted field in the change set, PR level first, then per column."""
        collected: list[UntrustedText] = list(self.untrusted_text)
        for change in self.column_changes:
            collected.extend(change.untrusted_text)
        return tuple(collected)


# ==========================================================================
# ImpactReport  --  produced by OWNER A (core/), consumed by OWNER B (ci/)
# ==========================================================================


class Transformation(_Model):
    type: TransformationType
    expression: str | None = None


class LineageHop(_Model):
    """One column-level edge on the path from the changed column to a consumer."""

    from_urn: Urn
    to_urn: Urn
    transformation: Transformation
    from_column: str | None = None
    to_column: str | None = None
    via_job_urn: str | None = None


class DownstreamEntity(_Model):
    """An entity reached through column-level lineage, with the path that proves it."""

    urn: Urn
    entity_type: EntityType
    name: Annotated[str, Field(pattern=r"^[^\n\r]+$", max_length=256)]
    hop_distance: Annotated[int, Field(ge=1)]
    path: Annotated[tuple[LineageHop, ...], Field(min_length=1)]
    platform: str | None = None
    via_column: str | None = None
    url: str | None = None


class Owner(_Model):
    urn: Annotated[str, Field(pattern=r"^urn:li:(corpuser|corpGroup):", max_length=512)]
    owner_type: OwnerType
    display_name: Annotated[str, Field(pattern=r"^[^\n\r]+$", max_length=128)]
    handle: str | None = None
    ownership_type: OwnershipType | None = None
    source: OwnerSource | None = None
    for_urn: str | None = None


class AssertionRef(_Model):
    urn: Annotated[str, Field(pattern=r"^urn:li:assertion:", max_length=512)]
    entity_urn: Urn
    assertion_type: AssertionType
    description: str | None = None
    last_result: AssertionResult | None = None
    references_changed_column: bool | None = None


class ContractRef(_Model):
    urn: Annotated[str, Field(pattern=r"^urn:li:dataContract:", max_length=512)]
    entity_urn: Urn
    state: ContractState
    name: str | None = None
    references_changed_column: bool | None = None


class QueryUsage(_Model):
    """Observed query volume. `unavailable` is a distinct state from zero."""

    window_days: Annotated[int, Field(ge=1, le=365)]
    query_count: Annotated[int, Field(ge=0)]
    source: UsageSource
    distinct_user_count: int | None = None
    last_queried_at: str | None = None


class SeverityFactor(_Model):
    """One term of the severity sum, with the graph fact it came from."""

    name: FactorName
    raw_value: RawFactorValue
    normalized: Annotated[float, Field(ge=0, le=1)]
    weight: Annotated[float, Field(ge=0, le=100)]
    contribution: Annotated[float, Field(ge=0, le=100)]
    description: str | None = None


class Severity(_Model):
    """A severity score and the complete breakdown that produced it.

    `computed_by` is a constant. There is no code path in this repository that
    can set it to anything else, because the module that builds this object
    cannot import the agent (proved by core/tests/test_module_boundaries.py).
    """

    score: Annotated[float, Field(ge=0, le=100)]
    level: SeverityLevel
    rule_version: RuleVersion
    factors: Annotated[tuple[SeverityFactor, ...], Field(min_length=7, max_length=7)]
    computed_by: Literal["deterministic"] = "deterministic"
    inputs_digest: Sha256 | None = None

    @model_validator(mode="after")
    def _factors_are_complete_and_unique(self) -> Severity:
        names = [f.name for f in self.factors]
        if len(set(names)) != len(names):
            msg = f"duplicate severity factors: {names}"
            raise ValueError(msg)
        return self


class Explanation(_Model):
    """The only model-generated content in the report."""

    text: Annotated[str, Field(max_length=4000)]
    generated_by: Literal["llm"] = "llm"
    is_model_generated: Literal[True] = True
    disclaimer: Literal[
        "Model-generated prose. Did not influence severity and did not gate any write."
    ] = "Model-generated prose. Did not influence severity and did not gate any write."
    model: str | None = None
    prompt_version: str | None = None
    untrusted_inputs_referenced: tuple[str, ...] = ()


class UntrustedFinding(_Model):
    """A heuristic detection of text addressed at the review agent.

    Reporting only. `effect_on_severity` is a constant because severity has
    already been computed from graph facts by the time this runs.
    """

    untrusted_text_id: UntrustedId
    pattern_id: Annotated[str, Field(pattern=r"^inj-[a-z0-9_]+$")]
    confidence: Literal["low", "medium", "high"]
    excerpt: Annotated[str, Field(max_length=500)]
    rationale: Annotated[str, Field(max_length=500)]
    effect_on_severity: Literal["none"] = "none"
    is_heuristic: Literal[True] = True
    detector_version: str | None = None


class FixValidation(_Model):
    """Whether the generated file actually compiles. Suggestion vs patch."""

    tool: Literal["dbt"] = "dbt"
    passed: bool
    command: Annotated[str, Field(pattern=r"^[^\n\r]+$", max_length=512)]
    exit_code: int | None = None
    attempts: int | None = None
    output_excerpt: str | None = None
    validated_at: str | None = None


class FixProvenance(_Model):
    model: Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]+$", max_length=64)]
    prompt_version: str | None = None
    attempts: int | None = None


class GeneratedFix(_Model):
    id: FixId
    path: Annotated[str, Field(pattern=r"^[^\n\r]+$", max_length=1024)]
    target_repo_path: Annotated[str, Field(pattern=r"^[^\n\r]+$", max_length=1024)]
    change_ids: Annotated[tuple[ChangeId, ...], Field(min_length=1)]
    validation: FixValidation
    target_repo: str | None = None
    language: FixLanguage | None = None
    sha256: Sha256 | None = None
    generated_by: FixProvenance | None = None


class Degradation(_Model):
    """A capability that was unavailable. Absence of data is never absence of impact."""

    capability: Capability
    reason: Annotated[str, Field(max_length=512)]
    consequence: str | None = None


class ColumnChangeRef(_Model):
    """The identifying subset of a ColumnChange, echoed into the report."""

    dbt_model: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]
    dataset_urn: Annotated[str, Field(pattern=r"^urn:li:dataset:\(", max_length=512)]
    dataset_name: Annotated[str, Field(pattern=r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$")]
    column: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]
    change_kind: ChangeKind


class ColumnImpact(_Model):
    change_id: ChangeId
    change: ColumnChangeRef
    query_usage: QueryUsage
    severity: Severity
    downstream: tuple[DownstreamEntity, ...] = ()
    owners_to_notify: tuple[Owner, ...] = ()
    assertions: tuple[AssertionRef, ...] = ()
    data_contracts: tuple[ContractRef, ...] = ()
    explanation: Explanation | None = None
    untrusted_findings: tuple[UntrustedFinding, ...] = ()
    fix_ids: tuple[FixId, ...] = ()


class DataHubContext(_Model):
    access_path: AccessPath
    queried_at: str
    lineage_max_hops: int | None = None
    usage_window_days: int | None = None
    server_version: str | None = None


class ToolRef(_Model):
    name: Literal["blast-radius"] = "blast-radius"
    version: Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]
    severity_rule_version: RuleVersion


class ChangeSetRef(_Model):
    pull_request: PullRequestRef
    change_set_sha256: Sha256


class ImpactReport(_Model):
    """The full output of the analysis. Consumed by ci/render and ci/publish.

    `schema_version` is 1.1.0 rather than 1.0.0 because `access_path` gained the
    value "mcp+sdk". Adding an enum value is a breaking change for a consumer
    that matches exhaustively, which `CONTRACT.md` records as requiring the bump.
    """

    schema_version: Literal["1.1.0"] = "1.1.0"
    generated_at: str
    tool: ToolRef
    change_set_ref: ChangeSetRef
    column_impacts: Annotated[tuple[ColumnImpact, ...], Field(min_length=1)]
    overall_severity: Severity
    datahub: DataHubContext | None = None
    generated_fixes: tuple[GeneratedFix, ...] = ()
    degradations: tuple[Degradation, ...] = ()


# ==========================================================================
# WritebackRecord  --  produced by OWNER A (core/writeback)
# ==========================================================================


class WritebackSeverity(_Model):
    score: Annotated[float, Field(ge=0, le=100)]
    level: SeverityLevel
    rule_version: RuleVersion


class WritebackColumn(_Model):
    dataset_urn: Annotated[str, Field(pattern=r"^urn:li:dataset:\(", max_length=512)]
    column: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=128)]
    change_kind: ChangeKind
    severity_level: SeverityLevel
    severity_score: float | None = None
    downstream_count: int | None = None
    new_name: str | None = None


class WritebackRecord(_Model):
    """The machine-readable finding left behind in DataHub for the next agent."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    pr_url: Annotated[str, Field(max_length=512)]
    changed_columns: Annotated[tuple[WritebackColumn, ...], Field(min_length=1)]
    severity: WritebackSeverity
    detected_at: str
    status: WritebackStatus
    repo: str | None = None
    pr_number: int | None = None
    downstream_entity_count: int | None = None
    downstream_urns: tuple[str, ...] = ()
    report_url: str | None = None
    fix_branch: str | None = None
    tool: ToolRef | None = None
    supersedes: str | None = None
    structured_property_urn: Literal["urn:li:structuredProperty:io.blastradius.impactRecord"] = (
        "urn:li:structuredProperty:io.blastradius.impactRecord"
    )
