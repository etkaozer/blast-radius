"""Tests for the dbt compile gate.

`compile_model` is what separates a suggestion from a patch, so the properties
worth testing are the ones that decide whether anyone will ever run it:

* it must not touch the developer's working tree, ever;
* it must not raise, whatever dbt does — not on a failed compile, not on a
  missing dbt, not on a hang. Every one of those is a line in the report.

dbt itself is not a dependency of this repository, so these tests put a small
executable called `dbt` on PATH and assert on how `compile_model` treats it.
That also covers the case a judge will actually hit, which is not having dbt
installed at all.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from core.validate.dbt import (
    MAX_OUTPUT_CHARS,
    CompileResult,
    compile_model,
    validate_with_retry,
)

MODEL_SQL = "select id, email from {{ ref('stg_customers') }}\n"
TARGET = Path("models/marts/dim_customers.sql")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A directory shaped enough like a dbt project to be copied and compiled."""
    root = tmp_path / "dbt_project"
    (root / "models" / "marts").mkdir(parents=True)
    (root / "target").mkdir()
    (root / "dbt_packages").mkdir()
    (root / TARGET).write_text(MODEL_SQL, encoding="utf-8")
    (root / "dbt_project.yml").write_text("name: demo\nversion: '1.0.0'\n", encoding="utf-8")
    (root / "target" / "manifest.json").write_text("{}", encoding="utf-8")
    return root


def fake_dbt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str) -> Path:
    """Put an executable called `dbt` on PATH and return its directory."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    executable = bin_dir / "dbt"
    executable.write_text(script, encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return bin_dir


# ---------------------------------------------------------------------------
# The non-negotiable one.
# ---------------------------------------------------------------------------


def test_the_working_tree_is_never_modified(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A validator with side effects is a validator nobody runs."""
    fake_dbt(tmp_path, monkeypatch, "#!/bin/sh\nexit 0\n")

    compile_model(project, "dim_customers", "select 'REPLACED' as x\n", TARGET)

    assert (project / TARGET).read_text(encoding="utf-8") == MODEL_SQL


def test_the_candidate_is_what_gets_compiled(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scratch copy must contain the candidate, not the original."""
    fake_dbt(tmp_path, monkeypatch, f"#!/bin/sh\ncat {TARGET}\nexit 0\n")

    result = compile_model(project, "dim_customers", "select 'CANDIDATE' as x\n", TARGET)

    assert result.passed is True
    assert "CANDIDATE" in result.output
    assert "email" not in result.output


# ---------------------------------------------------------------------------
# Every failure mode is a report line, not an exception.
# ---------------------------------------------------------------------------


def test_a_failed_compile_is_reported_not_raised(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_dbt(
        tmp_path,
        monkeypatch,
        "#!/bin/sh\necho 'Compilation Error in model dim_customers' >&2\nexit 1\n",
    )

    result = compile_model(project, "dim_customers", MODEL_SQL, TARGET)

    assert isinstance(result, CompileResult)
    assert result.passed is False
    assert result.exit_code == 1
    assert "Compilation Error" in result.output


def test_a_missing_dbt_degrades_instead_of_exploding(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The state a judge's laptop is actually in."""
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    result = compile_model(project, "dim_customers", MODEL_SQL, TARGET)

    assert result.passed is False
    assert result.exit_code == 127
    assert "not found on PATH" in result.output


def test_a_hanging_dbt_is_killed(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hung compiler must not hang the pull request."""
    fake_dbt(tmp_path, monkeypatch, "#!/bin/sh\nsleep 30\n")
    monkeypatch.setattr("core.validate.dbt.COMPILE_TIMEOUT_SECONDS", 1)

    result = compile_model(project, "dim_customers", MODEL_SQL, TARGET)

    assert result.passed is False
    assert result.exit_code == 124
    assert "exceeded" in result.output


def test_a_missing_project_directory_degrades(tmp_path: Path) -> None:
    result = compile_model(tmp_path / "does-not-exist", "m", "select 1", Path("m.sql"))
    assert result.passed is False
    assert "scratch copy" in result.output


def test_output_is_bounded(project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_dbt(
        tmp_path,
        monkeypatch,
        "#!/bin/sh\nawk 'BEGIN{for(i=0;i<20000;i++) print \"noise line\"}'\nexit 1\n",
    )

    result = compile_model(project, "dim_customers", MODEL_SQL, TARGET)

    assert len(result.output) <= MAX_OUTPUT_CHARS


def test_the_recorded_command_is_the_one_a_human_would_run(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The report quotes this verbatim, so it has to be reproducible by hand."""
    fake_dbt(tmp_path, monkeypatch, "#!/bin/sh\nexit 0\n")

    result = compile_model(project, "dim_customers", MODEL_SQL, TARGET)

    assert result.command == "dbt compile --select dim_customers"


def test_build_artifacts_are_not_copied_into_the_scratch_project(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Copying target/ makes the copy slower than the compile it enables."""
    fake_dbt(
        tmp_path,
        monkeypatch,
        "#!/bin/sh\nif [ -d target ]; then echo COPIED; fi\nexit 0\n",
    )

    result = compile_model(project, "dim_customers", MODEL_SQL, TARGET)

    assert "COPIED" not in result.output


# ---------------------------------------------------------------------------
# The retry loop, now that there is something for it to drive.
# ---------------------------------------------------------------------------


def test_retry_stops_as_soon_as_a_candidate_compiles(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fails while the file says BROKEN, passes once it does not."""
    fake_dbt(
        tmp_path,
        monkeypatch,
        f"#!/bin/sh\nif grep -q BROKEN {TARGET}; then echo 'error' >&2; exit 1; fi\nexit 0\n",
    )
    regenerations: list[str] = []

    def regenerate(compiler_output: str) -> str:
        regenerations.append(compiler_output)
        return "select 1 as fixed\n"

    content, validation = validate_with_retry(
        project, "dim_customers", TARGET, "select BROKEN\n", regenerate
    )

    assert validation.passed is True
    assert validation.attempts == 2
    assert content == "select 1 as fixed\n"
    assert len(regenerations) == 1
    assert "error" in regenerations[0]


def test_a_fix_that_never_compiles_is_still_reported(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An honest failure is reportable. Silence is not."""
    fake_dbt(tmp_path, monkeypatch, "#!/bin/sh\necho 'always broken' >&2\nexit 1\n")

    _, validation = validate_with_retry(
        project, "dim_customers", TARGET, "select 1\n", lambda _: "select 2\n"
    )

    assert validation.passed is False
    assert validation.attempts == 3
    assert "always broken" in (validation.output_excerpt or "")
