"""The analysis pipeline: eight stages, in an order that is itself a security control.

The ordering is the point. Severity is computed at stage 4, from graph facts
gathered at stage 3. The first time any untrusted prose is read is stage 5, and
the first time a model is called is stage 6. By then the number is fixed and the
only thing left for prose to influence is prose.

Stages that are implemented run for real. Stages that are not raise
`StubNotImplementedError`, which the CLI catches and turns into a report of
exactly what remains and who owns it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from contracts.loader import change_set_digest, load_change_set
from contracts.models import (
    ChangeSet,
    ChangeSetRef,
    ColumnImpact,
    DataHubContext,
    Degradation,
    GeneratedFix,
    ImpactReport,
    ToolRef,
)
from core.agent.client import AnthropicAgent
from core.config import Settings
from core.datahub.factory import build_reader
from core.errors import OWNER_A, StubNotImplementedError
from core.impact.analyzer import analyze_change_set
from core.severity.rules import RULE_VERSION
from core.severity.scoring import overall
from core.untrusted.detector import scan_all
from core.untrusted.envelope import UntrustedEnvelope, wrap_all
from core.version import VERSION

_T = "core.pipeline"


@dataclass(frozen=True, slots=True)
class Stage:
    """One pipeline stage, for progress reporting and for failure attribution."""

    key: str
    title: str
    module: str
    owner: str


STAGES: Final[tuple[Stage, ...]] = (
    Stage("load", "load and validate change set", "contracts.loader", OWNER_A),
    Stage("wrap", "wrap untrusted input", "core.untrusted.envelope", OWNER_A),
    Stage("impact", "DataHub impact analysis", "core.impact.analyzer", OWNER_A),
    Stage("severity", "deterministic severity scoring", "core.severity.scoring", OWNER_A),
    Stage("detect", "scan untrusted input", "core.untrusted.detector", OWNER_A),
    Stage("explain", "model-written explanation", "core.agent.client", OWNER_A),
    Stage("fixes", "generate and compile fixes", "core.validate.dbt", OWNER_A),
    Stage("report", "assemble and write report", "core.pipeline", OWNER_A),
)


@dataclass
class Progress:
    """Which stages ran, and where it stopped."""

    completed: list[tuple[Stage, str]] = field(default_factory=list)
    failed_stage: Stage | None = None

    def index_of(self, stage: Stage) -> int:
        """1-based position of a stage, for '3/8' style output."""
        return STAGES.index(stage) + 1


StageReporter = Callable[[Stage, str], None]


@dataclass(frozen=True, slots=True)
class AnalyzeRequest:
    """Everything one `blast-radius analyze` invocation needs."""

    change_set_path: Path
    out_path: Path
    fixes_dir: Path | None = None
    settings: Settings = field(default_factory=Settings)
    use_agent: bool = True
    generate_fixes: bool = True


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_analysis(
    request: AnalyzeRequest,
    progress: Progress,
    report_stage: StageReporter | None = None,
) -> ImpactReport:
    """Run the pipeline end to end, recording progress as it goes.

    Raises whatever an unimplemented stage raises. The caller is expected to
    catch `NotImplementedError` and use `progress` to say where it stopped.
    """

    def done(stage: Stage, detail: str) -> None:
        progress.completed.append((stage, detail))
        if report_stage is not None:
            report_stage(stage, detail)

    def start(stage: Stage) -> None:
        progress.failed_stage = stage

    settings = request.settings

    # -- 1. load and validate -------------------------------------------------
    start(STAGES[0])
    change_set: ChangeSet = load_change_set(request.change_set_path)
    untrusted_texts = change_set.all_untrusted_text()
    done(
        STAGES[0],
        f"PR #{change_set.pull_request.number} in {change_set.pull_request.repo}, "
        f"{len(change_set.column_changes)} column change(s), "
        f"{len(untrusted_texts)} untrusted text field(s)",
    )

    # -- 2. wrap untrusted input ---------------------------------------------
    # Before anything reads this text, and before any prompt exists.
    start(STAGES[1])
    envelopes: tuple[UntrustedEnvelope, ...] = wrap_all(untrusted_texts)
    done(STAGES[1], f"{len(envelopes)} envelope(s), content-addressed delimiters")

    # -- 3. ground in the metadata graph -------------------------------------
    start(STAGES[2])
    reader = build_reader(settings)
    impacts, degradations = analyze_change_set(change_set, reader, settings)
    done(STAGES[2], f"{len(impacts)} column impact(s)")

    # -- 4. severity ----------------------------------------------------------
    # Already computed per column inside the impact stage, from graph facts.
    # This stage only reduces them to a PR-level number.
    start(STAGES[3])
    overall_severity = overall(tuple(i.severity for i in impacts))
    done(STAGES[3], f"overall {overall_severity.score} ({overall_severity.level})")

    # -- 5. scan untrusted input ---------------------------------------------
    # AFTER severity. Findings are reported, never scored.
    start(STAGES[4])
    findings = scan_all(envelopes)
    impacts = _attach_findings(impacts, findings)
    done(STAGES[4], f"{len(findings)} finding(s), none affecting severity")

    # -- 6. explanation -------------------------------------------------------
    start(STAGES[5])
    if request.use_agent:
        settings.require_agent()
        agent = AnthropicAgent(api_key=settings.anthropic_api_key or "", model=settings.model)
        impacts = tuple(
            impact.model_copy(update={"explanation": agent.explain(impact, envelopes)})
            for impact in impacts
        )
        done(STAGES[5], f"{len(impacts)} explanation(s) from {settings.model}")
    else:
        degradations = (
            *degradations,
            Degradation(
                capability="llm_explanation",
                reason="--no-agent was passed",
                consequence="The report carries no prose. Severity and lineage are unaffected.",
            ),
        )
        done(STAGES[5], "skipped (--no-agent)")

    # -- 7. fixes -------------------------------------------------------------
    start(STAGES[6])
    fixes: tuple[GeneratedFix, ...] = ()
    if request.generate_fixes and request.fixes_dir is not None:
        fixes = generate_fixes(impacts, request.fixes_dir, settings)
        done(STAGES[6], f"{len(fixes)} fix file(s)")
    else:
        done(STAGES[6], "skipped (no --fixes-dir)")

    # -- 8. assemble ----------------------------------------------------------
    start(STAGES[7])
    report = ImpactReport(
        generated_at=_now(),
        tool=ToolRef(version=VERSION, severity_rule_version=RULE_VERSION),
        change_set_ref=ChangeSetRef(
            pull_request=change_set.pull_request,
            change_set_sha256=change_set_digest(change_set),
        ),
        datahub=DataHubContext(
            access_path=reader.access_path,
            queried_at=_now(),
            lineage_max_hops=settings.max_hops,
            usage_window_days=settings.usage_window_days,
        ),
        column_impacts=impacts,
        overall_severity=overall_severity,
        generated_fixes=fixes,
        degradations=degradations,
    )
    progress.failed_stage = None
    done(STAGES[7], f"{request.out_path}")
    return report


def _attach_findings(
    impacts: tuple[ColumnImpact, ...],
    findings: tuple[object, ...],
) -> tuple[ColumnImpact, ...]:
    """Attach untrusted findings to the impacts whose text produced them.

    Contract: match each finding to the column impact whose change carried the
    `untrusted_text_id`, and attach PR-level findings to every impact. Findings
    are attached AFTER severity has been computed and must never trigger a
    re-score.
    """
    raise StubNotImplementedError(
        f"{_T}._attach_findings",
        OWNER_A,
        "route untrusted findings to the ColumnImpact they came from, without re-scoring",
    )


def generate_fixes(
    impacts: tuple[ColumnImpact, ...],
    fixes_dir: Path,
    settings: Settings,
) -> tuple[GeneratedFix, ...]:
    """Generate and compile a candidate fix per affected downstream file.

    Contract: for each downstream dataset that blast-radius can map back to a
    file in the dbt project, ask `core.agent` for a candidate, run it through
    `core.validate.validate_with_retry`, write the result under `fixes_dir`, and
    record a `GeneratedFix` with the honest `validation` outcome. A fix that
    never compiled is still reported, marked as failed, with the compiler output
    attached.
    """
    raise StubNotImplementedError(
        f"{_T}.generate_fixes",
        OWNER_A,
        "per downstream file: propose_fix -> validate_with_retry -> write -> GeneratedFix",
    )
