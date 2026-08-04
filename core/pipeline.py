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
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Final

from contracts.loader import change_set_digest, load_change_set
from contracts.models import (
    ChangeSet,
    ChangeSetRef,
    ColumnImpact,
    DataHubContext,
    Degradation,
    FixProvenance,
    GeneratedFix,
    ImpactReport,
    PullRequestRef,
    ToolRef,
    UntrustedFinding,
)
from core.agent.client import AnthropicAgent
from core.agent.prompts import FIX_PROMPT_VERSION
from core.config import Settings
from core.datahub.factory import build_reader
from core.errors import OWNER_A, AgentError, ConfigurationError
from core.impact.analyzer import analyze_change_set
from core.severity.rules import RULE_VERSION
from core.severity.scoring import overall
from core.untrusted.detector import scan_all
from core.untrusted.envelope import UntrustedEnvelope, wrap_all
from core.validate.dbt import validate_with_retry
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
    impacts = _attach_findings(impacts, findings, change_set)
    done(STAGES[4], f"{len(findings)} finding(s), none affecting severity")

    # -- 6. explanation -------------------------------------------------------
    start(STAGES[5])
    if request.use_agent:
        settings.require_agent()
        agent = AnthropicAgent(api_key=settings.anthropic_api_key or "", model=settings.model)
        try:
            impacts = tuple(
                impact.model_copy(update={"explanation": agent.explain(impact, envelopes)})
                for impact in impacts
            )
        except AgentError as exc:
            # Prose is the last thing computed and the least load-bearing. A
            # review with a severity score, a lineage graph and no paragraph is
            # still a review; losing the whole run over it would not be.
            degradations = (
                *degradations,
                Degradation(
                    capability="llm_explanation",
                    reason=str(exc)[:512],
                    consequence=(
                        "The report carries no prose. Severity, lineage and the "
                        "untrusted findings are unaffected."
                    ),
                ),
            )
            done(STAGES[5], f"degraded ({exc})")
        else:
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
        fixes, fix_degradations = generate_fixes(impacts, request.fixes_dir, settings)
        degradations = (*degradations, *fix_degradations)
        impacts = _attach_fix_ids(impacts, fixes)
        verified = sum(1 for f in fixes if f.validation.passed)
        done(STAGES[6], f"{len(fixes)} fix file(s), {verified} verified by dbt compile")
    else:
        done(STAGES[6], "skipped (no --fixes-dir)")

    # -- 8. assemble ----------------------------------------------------------
    start(STAGES[7])
    report = ImpactReport(
        generated_at=_now(),
        tool=ToolRef(version=VERSION, severity_rule_version=RULE_VERSION),
        change_set_ref=ChangeSetRef(
            pull_request=_reportable_pull_request(change_set),
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


def _reportable_pull_request(change_set: ChangeSet) -> PullRequestRef:
    """Return the pull-request reference the IMPACT REPORT schema accepts.

    `change_set.schema.json` carries an `author`; the `change_set_ref` object in
    `impact_report.schema.json` does not, and is closed. Both models share one
    `PullRequestRef` in `contracts/models.py`, so copying the reference straight
    across emits a field the report's own schema rejects — and every fixture
    with an author would fail validation at the very last step of a run.

    Dropping it here loses nothing: the author is part of the change set, and
    the change set is pinned into the report by `change_set_sha256`.
    """
    return change_set.pull_request.model_copy(update={"author": None})


def _attach_findings(
    impacts: tuple[ColumnImpact, ...],
    findings: tuple[UntrustedFinding, ...],
    change_set: ChangeSet,
) -> tuple[ColumnImpact, ...]:
    """Attach untrusted findings to the impacts whose text produced them.

    Contract: match each finding to the column impact whose change carried the
    `untrusted_text_id`, and attach PR-level findings to every impact. Findings
    are attached AFTER severity has been computed and must never trigger a
    re-score.

    The routing needs the change set, because a `ColumnImpact` carries only the
    identifying subset of its change — the association between a column and the
    text attached to it lives in the `ChangeSet` and nowhere else.

    Attaching is `model_copy` onto one field. Severity is copied across
    untouched by construction: there is no code path here that could rebuild it
    even if a later edit wanted one.
    """
    per_change: dict[str, tuple[str, ...]] = {
        change.id: tuple(text.id for text in change.untrusted_text)
        for change in change_set.column_changes
    }
    pull_request_ids = frozenset(text.id for text in change_set.untrusted_text)

    attached: list[ColumnImpact] = []
    for impact in impacts:
        own = frozenset(per_change.get(impact.change_id, ()))
        # Column-level findings first, then the ones that came from the pull
        # request itself: the reviewer reads about the column they are looking
        # at before the PR description that wraps it.
        ordered = tuple(f for f in findings if f.untrusted_text_id in own) + tuple(
            f for f in findings if f.untrusted_text_id in pull_request_ids
        )
        attached.append(impact.model_copy(update={"untrusted_findings": ordered}))
    return tuple(attached)


@dataclass(frozen=True, slots=True)
class _FixTarget:
    """One downstream file a fix will be attempted against."""

    model_name: str
    absolute_path: Path
    repo_path: str
    impact: ColumnImpact
    change_ids: tuple[str, ...]


#: Directories a dbt model is never authored in. Searching them finds compiled
#: copies of the model and proposes a fix to a build artifact.
_NOT_SOURCE = frozenset({"target", "dbt_packages", "logs", ".git", ".venv"})


def _locate_dbt_model(project_dir: Path, model_name: str) -> Path | None:
    """Find the .sql file that defines `model_name`, or None.

    dbt requires model names to be unique across a project, so the file name is
    a reliable key. Sorted, so a project with an unexpected duplicate still
    picks the same one on every run rather than alternating between them.
    """
    candidates = sorted(
        path
        for path in project_dir.rglob(f"{model_name}.sql")
        if not _NOT_SOURCE & set(path.relative_to(project_dir).parts)
    )
    return candidates[0] if candidates else None


def _fix_targets(
    impacts: tuple[ColumnImpact, ...],
    project_dir: Path,
) -> tuple[tuple[_FixTarget, ...], tuple[str, ...]]:
    """Map downstream entities back onto files, and report the ones that did not map.

    One file can be downstream of several changed columns, and it gets ONE fix
    carrying every `change_id` that reached it — otherwise two column changes in
    the same PR produce two conflicting rewrites of the same model.
    """
    found: dict[str, _FixTarget] = {}
    unmapped: list[str] = []

    for impact in impacts:
        for entity in impact.downstream:
            # Fix generation is scoped to dbt SQL models. A dashboard is real
            # impact and is reported as such, but it is not a file we can patch.
            if entity.entity_type != "dataset":
                continue
            model_name = entity.name.rsplit(".", maxsplit=1)[-1]
            path = _locate_dbt_model(project_dir, model_name)
            if path is None:
                unmapped.append(entity.name)
                continue
            repo_path = path.relative_to(project_dir).as_posix()
            existing = found.get(repo_path)
            if existing is None:
                found[repo_path] = _FixTarget(
                    model_name=model_name,
                    absolute_path=path,
                    repo_path=repo_path,
                    impact=impact,
                    change_ids=(impact.change_id,),
                )
            elif impact.change_id not in existing.change_ids:
                found[repo_path] = replace(
                    existing, change_ids=(*existing.change_ids, impact.change_id)
                )

    return tuple(found[key] for key in sorted(found)), tuple(dict.fromkeys(unmapped))


def generate_fixes(
    impacts: tuple[ColumnImpact, ...],
    fixes_dir: Path,
    settings: Settings,
) -> tuple[tuple[GeneratedFix, ...], tuple[Degradation, ...]]:
    """Generate and compile a candidate fix per affected downstream file.

    Contract: for each downstream dataset that blast-radius can map back to a
    file in the dbt project, ask `core.agent` for a candidate, run it through
    `core.validate.validate_with_retry`, write the result under `fixes_dir`, and
    record a `GeneratedFix` with the honest `validation` outcome. A fix that
    never compiled is still reported, marked as failed, with the compiler output
    attached.

    Returns the degradations alongside the fixes rather than swallowing them: a
    downstream model that could not be located is the reviewer's problem to know
    about, not ours to hide.
    """
    degradations: list[Degradation] = []

    if not settings.dbt_project_dir:
        return (), (
            Degradation(
                capability="fix_generation",
                reason="BLAST_RADIUS_DBT_PROJECT_DIR is not set",
                consequence=(
                    "No fixes were generated. Severity, lineage and the untrusted "
                    "findings are unaffected."
                ),
            ),
        )

    project_dir = Path(settings.dbt_project_dir)
    if not project_dir.is_dir():
        return (), (
            Degradation(
                capability="fix_generation",
                reason=f"dbt project directory {project_dir} does not exist",
                consequence="No fixes were generated.",
            ),
        )

    agent = AnthropicAgent(api_key=settings.anthropic_api_key or "", model=settings.model)
    targets, unmapped = _fix_targets(impacts, project_dir)

    if unmapped:
        degradations.append(
            Degradation(
                capability="fix_generation",
                reason=f"no dbt model file found for: {', '.join(unmapped)}"[:512],
                consequence=(
                    "Those entities are affected and are reported, but no fix was "
                    "attempted for them. They may live in another repository."
                ),
            )
        )

    fixes: list[GeneratedFix] = []
    for index, target in enumerate(targets, start=1):
        try:
            current = target.absolute_path.read_text(encoding="utf-8")
        except OSError as exc:
            degradations.append(
                Degradation(
                    capability="fix_generation",
                    reason=f"could not read {target.repo_path}: {exc}"[:512],
                    consequence="No fix was attempted for this file.",
                )
            )
            continue

        # `validate_with_retry` owns the attempt budget; this counter only
        # carries the attempt number into the report's provenance block.
        attempt = 1

        # `target` and `current` are bound as defaults rather than captured:
        # the closure outlives this iteration of the loop, and a late-bound
        # `current` would resend the LAST file's contents on every retry.
        def regenerate(
            compiler_output: str,
            target: _FixTarget = target,
            current: str = current,
        ) -> str:
            nonlocal attempt
            attempt += 1
            return agent.propose_fix(
                target.impact,
                target.repo_path,
                current,
                compiler_output=compiler_output,
                attempt=attempt,
            ).content

        try:
            candidate = agent.propose_fix(target.impact, target.repo_path, current, attempt=1)
            content, validation = validate_with_retry(
                project_dir,
                target.model_name,
                Path(target.repo_path),
                candidate.content,
                regenerate,
            )
        except (AgentError, ConfigurationError) as exc:
            degradations.append(
                Degradation(
                    capability="fix_generation",
                    reason=f"{target.repo_path}: {exc}"[:512],
                    consequence="No fix was produced for this file. The analysis is unaffected.",
                )
            )
            continue

        written = fixes_dir / Path(target.repo_path).name
        try:
            written.parent.mkdir(parents=True, exist_ok=True)
            written.write_text(content, encoding="utf-8")
        except OSError as exc:
            degradations.append(
                Degradation(
                    capability="fix_generation",
                    reason=f"could not write {written}: {exc}"[:512],
                    consequence="The fix was generated but not saved.",
                )
            )
            continue

        if not validation.passed:
            degradations.append(
                Degradation(
                    capability="fix_validation",
                    reason=f"{target.repo_path} did not compile after "
                    f"{validation.attempts} attempt(s)",
                    consequence=(
                        "The file is reported as an unverified suggestion, not a patch. "
                        "The compiler output is attached to the fix."
                    ),
                )
            )

        fixes.append(
            GeneratedFix(
                id=f"fix-{index}",
                path=Path(target.repo_path).name,
                target_repo_path=target.repo_path,
                change_ids=target.change_ids,
                validation=validation,
                language=candidate.language,  # type: ignore[arg-type]
                sha256=sha256(content.encode("utf-8")).hexdigest(),
                generated_by=FixProvenance(
                    model=settings.model,
                    prompt_version=FIX_PROMPT_VERSION,
                    attempts=validation.attempts,
                ),
            )
        )

    return tuple(fixes), tuple(degradations)


def _attach_fix_ids(
    impacts: tuple[ColumnImpact, ...],
    fixes: tuple[GeneratedFix, ...],
) -> tuple[ColumnImpact, ...]:
    """Point each impact at the fixes generated for it, preserving fix order."""
    if not fixes:
        return impacts
    return tuple(
        impact.model_copy(
            update={
                "fix_ids": tuple(f.id for f in fixes if impact.change_id in f.change_ids),
            }
        )
        for impact in impacts
    )
