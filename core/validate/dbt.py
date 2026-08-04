"""Compile generated fixes against a real dbt project.

This module is what separates blast-radius from a tool that pastes plausible SQL
into a PR comment. A candidate fix is a suggestion until `dbt compile` accepts
it; after that it is a patch, and the report labels it differently.

The retry loop lives here rather than in `core.agent` on purpose: the thing that
decides whether to spend another model call is the thing holding the compiler
output, and it is bounded by a constant in this file that a prompt cannot argue
with.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from contracts.models import FixValidation

_T = "core.validate.dbt"

#: Total attempts per file, including the first. Three is enough to fix a typo
#: or a missing ref and not enough to burn a budget on a fix that will never
#: compile.
MAX_ATTEMPTS: Final[int] = 3

#: Compiler output is quoted into the report; this bound matches
#: `fixValidation.output_excerpt` in the schema.
MAX_OUTPUT_CHARS: Final[int] = 8000

#: Wall-clock budget for one `dbt compile`. A hung dbt must not hang a review:
#: the PR comment is worth more on time and honest than late and complete.
COMPILE_TIMEOUT_SECONDS: Final[int] = 120

#: Never copied into the scratch project. `target/` and `dbt_packages/` are
#: build output that dbt regenerates, and copying them makes the copy slower
#: than the compile it exists to enable.
_IGNORED = shutil.ignore_patterns(
    "target", "dbt_packages", "logs", ".git", ".venv", "__pycache__", "*.duckdb.wal"
)

#: Exit codes for the failures that are ours rather than dbt's. 127 and 124 are
#: the shell's conventions for "command not found" and "timed out", and reusing
#: them means a reader of the report does not need our private vocabulary.
_EXIT_DBT_MISSING: Final[int] = 127
_EXIT_TIMEOUT: Final[int] = 124
_EXIT_INTERNAL: Final[int] = 1


@dataclass(frozen=True, slots=True)
class CompileResult:
    """Raw outcome of one `dbt` invocation."""

    passed: bool
    command: str
    exit_code: int
    output: str


def compile_model(
    project_dir: Path, model_name: str, content: str, target_path: Path
) -> CompileResult:
    """Write `content` to `target_path` inside a scratch copy of the project and compile it.

    Contract:

    - NEVER modify the developer's working tree. Copy the dbt project (or use a
      git worktree) into a temporary directory, apply the candidate there, and
      compile there. A validator with side effects is a validator nobody runs.
    - Run `dbt compile --select <model_name>`, not `dbt build` and not `dbt run`:
      compilation catches the reference errors that schema changes cause,
      without touching the warehouse or costing warehouse credits.
    - Capture stdout and stderr together, truncate to `MAX_OUTPUT_CHARS` keeping
      the TAIL, because the useful part of a dbt error is at the end.
    - Return `CompileResult`, do not raise on a non-zero exit: a failed compile
      is an expected outcome that gets reported, not an exception.
    - Time out. A hung dbt process must not hang the review.
    """
    command = f"dbt compile --select {model_name}"
    with tempfile.TemporaryDirectory(prefix="blast-radius-dbt-") as scratch_root:
        scratch = Path(scratch_root) / project_dir.name

        # Preparing the copy and running the compiler fail in ways that look
        # alike to `except` and nothing alike to a reader. A missing project
        # directory reported as "dbt is not installed" costs somebody an hour,
        # so the two are caught separately and never share a message.
        try:
            shutil.copytree(project_dir, scratch, ignore=_IGNORED, symlinks=True)
            destination = scratch / _relative_to_project(project_dir, target_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
        except OSError as exc:
            return CompileResult(
                passed=False,
                command=command,
                exit_code=_EXIT_INTERNAL,
                output=f"could not prepare a scratch copy of {project_dir}: {exc}",
            )

        argv = ["dbt", "compile", "--select", model_name]
        if (scratch / "profiles.yml").is_file():
            # dbt looks in ~/.dbt otherwise, which in CI is empty.
            argv += ["--profiles-dir", "."]

        try:
            completed = subprocess.run(  # fixed argv, never a shell string
                argv,
                cwd=scratch,
                capture_output=True,
                text=True,
                timeout=COMPILE_TIMEOUT_SECONDS,
                check=False,
            )
        except FileNotFoundError:
            return CompileResult(
                passed=False,
                command=command,
                exit_code=_EXIT_DBT_MISSING,
                output=(
                    "dbt was not found on PATH. Generated fixes are reported as unverified "
                    "suggestions rather than as patches; install dbt to have them compiled."
                ),
            )
        except subprocess.TimeoutExpired:
            return CompileResult(
                passed=False,
                command=command,
                exit_code=_EXIT_TIMEOUT,
                output=f"dbt compile exceeded {COMPILE_TIMEOUT_SECONDS}s and was terminated.",
            )
        except OSError as exc:
            return CompileResult(
                passed=False,
                command=command,
                exit_code=_EXIT_INTERNAL,
                output=f"could not run dbt: {exc}",
            )

    return CompileResult(
        passed=completed.returncode == 0,
        command=command,
        exit_code=completed.returncode,
        output=truncate_output((completed.stdout or "") + (completed.stderr or "")),
    )


def _relative_to_project(project_dir: Path, target_path: Path) -> Path:
    """Return `target_path` as a path inside the project, however it was given.

    Callers hold either a repo-relative path from the change set
    (`models/marts/dim_customers.sql`) or an absolute one they resolved
    themselves. Both have to land in the same place in the scratch copy.
    """
    if not target_path.is_absolute():
        return target_path
    try:
        return target_path.relative_to(project_dir.resolve())
    except ValueError:
        # Absolute but outside the project: keep only the file name rather than
        # writing outside the scratch directory.
        return Path(target_path.name)


def truncate_output(output: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    """Keep the tail of compiler output, where dbt puts the actual error."""
    if len(output) <= limit:
        return output
    return "…\n" + output[-(limit - 2) :]


def validate_with_retry(
    project_dir: Path,
    model_name: str,
    target_path: Path,
    initial_content: str,
    regenerate: Callable[[str], str],
    max_attempts: int = MAX_ATTEMPTS,
) -> tuple[str, FixValidation]:
    """Compile a candidate, asking `regenerate` for a new one on failure.

    Real implementation: the loop is control flow, and control flow that decides
    how many times to call a model should not itself be waiting on a model to be
    written. `compile_model` and `regenerate` are the parts that are stubbed.

    `regenerate` receives the compiler output and returns replacement contents.
    Returns the last candidate tried and its validation record, whether or not
    it passed — an honest failure is reportable, and the report says so.
    """
    if max_attempts < 1:
        msg = f"max_attempts must be at least 1, got {max_attempts}"
        raise ValueError(msg)

    content = initial_content
    result: CompileResult | None = None

    for attempt in range(1, max_attempts + 1):
        result = compile_model(project_dir, model_name, content, target_path)
        if result.passed:
            return content, FixValidation(
                passed=True,
                command=result.command,
                exit_code=result.exit_code,
                attempts=attempt,
            )
        if attempt < max_attempts:
            content = regenerate(truncate_output(result.output))

    assert result is not None
    return content, FixValidation(
        passed=False,
        command=result.command,
        exit_code=result.exit_code,
        attempts=max_attempts,
        output_excerpt=truncate_output(result.output),
    )
