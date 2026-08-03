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

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from contracts.models import FixValidation
from core.errors import OWNER_A, StubNotImplementedError

_T = "core.validate.dbt"

#: Total attempts per file, including the first. Three is enough to fix a typo
#: or a missing ref and not enough to burn a budget on a fix that will never
#: compile.
MAX_ATTEMPTS: Final[int] = 3

#: Compiler output is quoted into the report; this bound matches
#: `fixValidation.output_excerpt` in the schema.
MAX_OUTPUT_CHARS: Final[int] = 8000


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
    raise StubNotImplementedError(
        f"{_T}.compile_model",
        OWNER_A,
        "apply the candidate in a scratch copy and run `dbt compile --select <model>`; "
        "return CompileResult without raising on failure",
    )


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
