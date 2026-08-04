"""Finding routing and fix generation.

Two separable jobs live in `core/pipeline.py` beyond stage sequencing, and both
are the kind of plumbing that is wrong in a way nobody notices until a demo:

* `_attach_findings` decides which column impact a piece of injected text is
  reported against. Get it wrong and the PR comment shows the attack under the
  wrong column, or shows it three times.
* `generate_fixes` decides which files to rewrite. Get it wrong and two changed
  columns produce two conflicting rewrites of the same downstream model.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from contracts.loader import fixture_dir, load_change_set, load_impact_report
from contracts.models import ColumnImpact, FixValidation, GeneratedFix
from core.agent.client import FixCandidate
from core.config import Settings
from core.errors import AgentError
from core.pipeline import (
    _attach_findings,
    _attach_fix_ids,
    _locate_dbt_model,
    generate_fixes,
)
from core.untrusted.detector import scan_all
from core.untrusted.envelope import wrap_all

ADVERSARIAL = fixture_dir("03_adversarial_description")


def adversarial_impact() -> ColumnImpact:
    return load_impact_report(ADVERSARIAL / "expected_impact_report.json").column_impacts[0]


# ---------------------------------------------------------------------------
# Routing findings back to the column they came from
# ---------------------------------------------------------------------------


def test_findings_are_routed_to_the_column_whose_text_produced_them() -> None:
    change_set = load_change_set(ADVERSARIAL / "change_set.json")
    findings = scan_all(wrap_all(change_set.all_untrusted_text()))

    attached = _attach_findings((adversarial_impact(),), findings, change_set)

    assert len(attached) == 1
    assert len(attached[0].untrusted_findings) == 3


def test_column_findings_are_ordered_before_pull_request_findings() -> None:
    """The reviewer reads about the column they are looking at first."""
    change_set = load_change_set(ADVERSARIAL / "change_set.json")
    findings = scan_all(wrap_all(change_set.all_untrusted_text()))
    column_ids = {t.id for t in change_set.column_changes[0].untrusted_text}
    pr_ids = {t.id for t in change_set.untrusted_text}

    attached = _attach_findings((adversarial_impact(),), findings, change_set)
    order = [f.untrusted_text_id for f in attached[0].untrusted_findings]

    assert set(order[:2]) == column_ids
    assert set(order[2:]) == pr_ids


def test_attaching_findings_cannot_change_a_score() -> None:
    """The claim the whole project rests on, at the point it would be violated."""
    change_set = load_change_set(ADVERSARIAL / "change_set.json")
    findings = scan_all(wrap_all(change_set.all_untrusted_text()))
    before = adversarial_impact()

    after = _attach_findings((before,), findings, change_set)[0]

    assert after.severity == before.severity
    assert after.severity.score == 77.0
    assert all(f.effect_on_severity == "none" for f in after.untrusted_findings)


def test_a_column_with_no_text_of_its_own_still_sees_pull_request_findings() -> None:
    change_set = load_change_set(ADVERSARIAL / "change_set.json")
    stripped = change_set.model_copy(
        update={
            "column_changes": (
                change_set.column_changes[0].model_copy(update={"untrusted_text": ()}),
            )
        }
    )
    findings = scan_all(wrap_all(stripped.all_untrusted_text()))

    attached = _attach_findings((adversarial_impact(),), findings, stripped)

    assert len(attached[0].untrusted_findings) == 1


def test_a_clean_change_set_attaches_nothing() -> None:
    change_set = load_change_set(fixture_dir("01_rename") / "change_set.json")
    impact = load_impact_report(
        fixture_dir("01_rename") / "expected_impact_report.json"
    ).column_impacts[0]
    findings = scan_all(wrap_all(change_set.all_untrusted_text()))

    assert _attach_findings((impact,), findings, change_set)[0].untrusted_findings == ()


# ---------------------------------------------------------------------------
# Locating the file behind a downstream entity
# ---------------------------------------------------------------------------


@pytest.fixture
def dbt_project(tmp_path: Path) -> Path:
    root = tmp_path / "dbt_project"
    (root / "models" / "marts").mkdir(parents=True)
    (root / "models" / "staging").mkdir(parents=True)
    (root / "target" / "compiled").mkdir(parents=True)
    (root / "models" / "marts" / "dim_customers.sql").write_text("select 1", encoding="utf-8")
    (root / "models" / "marts" / "customer_ltv.sql").write_text("select 2", encoding="utf-8")
    # A compiled copy of the same model. Proposing a fix to this would be a bug.
    (root / "target" / "compiled" / "dim_customers.sql").write_text("compiled", encoding="utf-8")
    return root


def test_a_model_is_found_by_name(dbt_project: Path) -> None:
    found = _locate_dbt_model(dbt_project, "dim_customers")
    assert found is not None
    assert found.relative_to(dbt_project).as_posix() == "models/marts/dim_customers.sql"


def test_build_output_is_never_mistaken_for_a_source_model(dbt_project: Path) -> None:
    found = _locate_dbt_model(dbt_project, "dim_customers")
    assert found is not None
    assert "target" not in found.parts


def test_an_unknown_model_returns_none(dbt_project: Path) -> None:
    assert _locate_dbt_model(dbt_project, "no_such_model") is None


# ---------------------------------------------------------------------------
# Fix generation
# ---------------------------------------------------------------------------


class StubAgent:
    """Stands in for `AnthropicAgent` at the seam `generate_fixes` constructs."""

    def __init__(self, api_key: str = "", model: str = "claude-sonnet-5") -> None:
        self.model = model
        self.calls: list[str] = []

    def propose_fix(
        self,
        impact: ColumnImpact,
        target_repo_path: str,
        current_content: str,
        compiler_output: str | None = None,
        attempt: int = 1,
    ) -> FixCandidate:
        self.calls.append(target_repo_path)
        return FixCandidate(
            target_repo_path=target_repo_path,
            content=f"-- fixed {target_repo_path}\nselect 1\n",
            language="sql",
            attempts=attempt,
        )


def install_stub_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the agent `generate_fixes` constructs with one that never calls out."""
    monkeypatch.setattr("core.pipeline.AnthropicAgent", StubAgent)


def impact_with_downstream(names: tuple[str, ...]) -> ColumnImpact:
    """An impact whose downstream entities point at the given dataset names."""
    base = load_impact_report(
        fixture_dir("01_rename") / "expected_impact_report.json"
    ).column_impacts[0]
    template = base.downstream[0]
    return base.model_copy(
        update={
            "downstream": tuple(
                template.model_copy(update={"name": name, "urn": f"urn:li:dataset:({name})"})
                for name in names
            )
        }
    )


def test_no_dbt_project_configured_degrades_rather_than_crashing(tmp_path: Path) -> None:
    fixes, degradations = generate_fixes((adversarial_impact(),), tmp_path, Settings())

    assert fixes == ()
    assert [d.capability for d in degradations] == ["fix_generation"]
    assert "BLAST_RADIUS_DBT_PROJECT_DIR" in degradations[0].reason


def test_a_nonexistent_dbt_project_degrades(tmp_path: Path) -> None:
    settings = Settings(dbt_project_dir=str(tmp_path / "nope"))
    fixes, degradations = generate_fixes((adversarial_impact(),), tmp_path, settings)

    assert fixes == ()
    assert degradations[0].capability == "fix_generation"


def test_a_fix_is_written_and_recorded(
    dbt_project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_stub_agent(monkeypatch)
    # No dbt on PATH: the compile honestly fails, and the fix is still reported.
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    settings = Settings(dbt_project_dir=str(dbt_project))
    out = tmp_path / "fixes"

    fixes, degradations = generate_fixes(
        (impact_with_downstream(("analytics.dbt_prod.dim_customers",)),), out, settings
    )

    assert len(fixes) == 1
    fix = fixes[0]
    assert isinstance(fix, GeneratedFix)
    assert fix.target_repo_path == "models/marts/dim_customers.sql"
    assert fix.path == "dim_customers.sql"
    assert (out / "dim_customers.sql").read_text(encoding="utf-8").startswith("-- fixed")
    assert fix.sha256 is not None
    # dbt was absent, so the fix is a suggestion and the report says so.
    assert fix.validation.passed is False
    assert "fix_validation" in {d.capability for d in degradations}


def test_two_columns_hitting_one_file_produce_one_fix(
    dbt_project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otherwise the two rewrites of the same model conflict."""
    install_stub_agent(monkeypatch)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    settings = Settings(dbt_project_dir=str(dbt_project))

    first = impact_with_downstream(("analytics.dbt_prod.dim_customers",))
    second = first.model_copy(update={"change_id": "cc-2"})

    fixes, _ = generate_fixes((first, second), tmp_path / "fixes", settings)

    assert len(fixes) == 1
    assert set(fixes[0].change_ids) == {"cc-1", "cc-2"}


def test_an_unmappable_downstream_entity_is_reported_not_skipped(
    dbt_project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_stub_agent(monkeypatch)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    settings = Settings(dbt_project_dir=str(dbt_project))

    _, degradations = generate_fixes(
        (impact_with_downstream(("analytics.dbt_prod.lives_elsewhere",)),),
        tmp_path / "fixes",
        settings,
    )

    reasons = [d.reason for d in degradations if d.capability == "fix_generation"]
    assert any("lives_elsewhere" in r for r in reasons)


def test_an_agent_failure_degrades_that_file_only(
    dbt_project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class ExplodingAgent(StubAgent):
        def propose_fix(self, *args: object, **kwargs: object) -> FixCandidate:
            error = AgentError("connection reset")
            raise error

    monkeypatch.setattr("core.pipeline.AnthropicAgent", ExplodingAgent)
    settings = Settings(dbt_project_dir=str(dbt_project))

    fixes, degradations = generate_fixes(
        (impact_with_downstream(("analytics.dbt_prod.dim_customers",)),),
        tmp_path / "fixes",
        settings,
    )

    assert fixes == ()
    assert any("connection reset" in d.reason for d in degradations)


def test_non_dataset_consumers_are_not_given_fixes(
    dbt_project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dashboard is real impact and is reported. It is not a file we can patch."""
    install_stub_agent(monkeypatch)
    settings = Settings(dbt_project_dir=str(dbt_project))
    impact = impact_with_downstream(("analytics.dbt_prod.dim_customers",))
    dashboard = impact.downstream[0].model_copy(
        update={"entity_type": "dashboard", "name": "Revenue Overview"}
    )
    impact = impact.model_copy(update={"downstream": (dashboard,)})

    fixes, degradations = generate_fixes((impact,), tmp_path / "fixes", settings)

    assert fixes == ()
    assert not any("Revenue Overview" in (d.reason or "") for d in degradations)


# ---------------------------------------------------------------------------
# Linking fixes back to impacts
# ---------------------------------------------------------------------------


def test_fix_ids_are_attached_to_the_impacts_they_came_from() -> None:
    impact = adversarial_impact()
    fix = GeneratedFix(
        id="fix-1",
        path="dim.sql",
        target_repo_path="models/marts/dim.sql",
        change_ids=(impact.change_id,),
        validation=_passing_validation(),
    )

    assert _attach_fix_ids((impact,), (fix,))[0].fix_ids == ("fix-1",)


def test_an_impact_with_no_fixes_gets_no_ids() -> None:
    impact = adversarial_impact()
    fix = GeneratedFix(
        id="fix-1",
        path="dim.sql",
        target_repo_path="models/marts/dim.sql",
        change_ids=("cc-9",),
        validation=_passing_validation(),
    )

    assert _attach_fix_ids((impact,), (fix,))[0].fix_ids == ()


def _passing_validation() -> FixValidation:
    return FixValidation(passed=True, command="dbt compile --select dim", exit_code=0, attempts=1)
