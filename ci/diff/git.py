"""Reading the two revisions of a pull request out of git.

`ci/diff/extract.py` diffs two strings. Something has to produce those strings,
and on a pull request that something is git: the base and head SHAs come from
the GitHub event payload, and the files that changed between them are the files
worth parsing.

Everything here shells out to `git` with an argument list — never a shell
string — and every SHA is checked against `^[0-9a-f]{7,40}$` before it reaches
an argument position. A SHA arrives from an event payload, which is an input
like any other, and `git` has a long list of options that take a path or a
command.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from ci.diff.extract import FileDiff
from contracts.errors import BlastRadiusError

#: dbt's default `model-paths`. A project that sets its own passes them in.
DEFAULT_MODEL_PATHS: Final[tuple[str, ...]] = ("models",)

_SHA = re.compile(r"^[0-9a-f]{7,40}$")
_TIMEOUT_SECONDS: Final[int] = 30

#: Statuses `git diff --name-status` can report. A rename or copy carries two
#: paths; everything else carries one.
_TWO_PATH_STATUSES: Final[str] = "RC"


class GitError(BlastRadiusError):
    """A git command failed, or was asked for something that does not exist."""


@dataclass(frozen=True, slots=True)
class ChangedFile:
    """One file git reports as changed between two revisions."""

    status: str
    path: str
    previous_path: str | None = None

    @property
    def added(self) -> bool:
        return self.status.startswith("A")

    @property
    def deleted(self) -> bool:
        return self.status.startswith("D")


def _checked_sha(sha: str) -> str:
    if not _SHA.match(sha):
        msg = f"{sha!r} is not a git object id"
        raise GitError(msg)
    return sha


def _git(args: list[str], repo_dir: Path) -> str:
    """Run a git command in `repo_dir` and return stdout, or raise GitError."""
    try:
        # An argument list, never a shell string, and never `shell=True`.
        completed = subprocess.run(
            ["git", *args],
            cwd=repo_dir,
            capture_output=True,
            check=True,
            timeout=_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        msg = "git is not installed or not on PATH"
        raise GitError(msg) from exc
    except subprocess.TimeoutExpired as exc:
        msg = f"git {args[0]} timed out after {_TIMEOUT_SECONDS}s"
        raise GitError(msg) from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace").strip()
        msg = f"git {' '.join(args)} failed: {detail}"
        raise GitError(msg) from exc
    return completed.stdout.decode("utf-8", errors="replace")


def repository_root(start: Path | None = None) -> Path:
    """Return the top level of the git repository containing `start`."""
    return Path(_git(["rev-parse", "--show-toplevel"], start or Path.cwd()).strip())


def changed_files(
    base_sha: str,
    head_sha: str,
    repo_dir: Path,
    pathspec: list[str] | None = None,
) -> tuple[ChangedFile, ...]:
    """Return the files that differ between two revisions.

    Uses `-z` so that a path containing a space, a quote or a non-ASCII
    character arrives intact rather than in git's quoted form, and `-M` so that
    a renamed file is one entry with both paths instead of a delete and an add.
    """
    args = [
        "diff",
        "--name-status",
        "-M",
        "-z",
        _checked_sha(base_sha),
        _checked_sha(head_sha),
    ]
    if pathspec:
        args.extend(["--", *pathspec])

    tokens = _git(args, repo_dir).split("\0")
    changed: list[ChangedFile] = []
    index = 0
    while index < len(tokens) and tokens[index]:
        status = tokens[index]
        index += 1
        if status[0] in _TWO_PATH_STATUSES:
            previous, path = tokens[index], tokens[index + 1]
            index += 2
        else:
            previous, path = None, tokens[index]
            index += 1
        changed.append(ChangedFile(status=status, path=path, previous_path=previous))
    return tuple(changed)


def read_blob(sha: str, path: str, repo_dir: Path) -> str | None:
    """Return the contents of `path` at `sha`, or None if it did not exist then."""
    try:
        return _git(["show", f"{_checked_sha(sha)}:{path}"], repo_dir)
    except GitError:
        return None


def _is_model(path: str, project_prefix: str, model_paths: tuple[str, ...]) -> bool:
    """True for a dbt model file inside the project we were pointed at."""
    if not path.endswith(".sql"):
        return False
    if project_prefix and not path.startswith(f"{project_prefix}/"):
        return False
    remainder = path[len(project_prefix) + 1 :] if project_prefix else path
    return any(remainder.startswith(f"{root}/") for root in model_paths)


def collect_file_diffs(
    base_sha: str,
    head_sha: str,
    repo_dir: Path | None = None,
    project_dir: Path | None = None,
    model_paths: tuple[str, ...] = DEFAULT_MODEL_PATHS,
) -> tuple[FileDiff, ...]:
    """Build a `FileDiff` for every dbt model the pull request touched.

    Paths in the result are relative to the **dbt project**, not to the
    repository: `models/staging/stg_customers.sql`. That is what a dbt manifest
    speaks, what `contracts/change_set.schema.json` carries in `file_path`, and
    what a reader recognises. When the project sits at the repository root the
    two are the same string, and when it does not, this is the only place that
    has to know.

    A deleted model has no head revision and an added one has no base; both are
    passed through rather than skipped, because a removed model is exactly the
    kind of change this tool exists to catch.
    """
    root = (repo_dir or repository_root()).resolve()
    project = (project_dir or root).resolve()
    try:
        prefix = str(project.relative_to(root)) if project != root else ""
    except ValueError as exc:
        msg = f"{project} is not inside the git repository at {root}"
        raise GitError(msg) from exc

    pathspec = [f"{prefix}/" if prefix else "."]
    diffs: list[FileDiff] = []

    for changed in changed_files(base_sha, head_sha, root, pathspec):
        if not _is_model(changed.path, prefix, model_paths):
            continue
        base_path = changed.previous_path or changed.path
        diffs.append(
            FileDiff(
                path=_relative(changed.path, prefix),
                base_content=None if changed.added else read_blob(base_sha, base_path, root),
                head_content=None if changed.deleted else read_blob(head_sha, changed.path, root),
            )
        )
    return tuple(diffs)


def _relative(path: str, prefix: str) -> str:
    return path[len(prefix) + 1 :] if prefix else path
