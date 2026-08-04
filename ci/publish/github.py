"""Publish the review: post the comment, push the fixes.

Two side effects on someone else's repository, so both are written to be safe to
re-run. A CI job that posts a new comment on every push turns a useful tool into
a reason to disable the tool.

The API client is `urllib` rather than a dependency. Three endpoints do not
justify one, and the transport is a parameter, which is what makes the rules
below testable without a network.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Final, Protocol

from ci.diff.git import GitError, run_git
from ci.render.markdown import COMMENT_MARKER
from contracts.errors import BlastRadiusError
from contracts.models import GeneratedFix, ImpactReport

logger = logging.getLogger(__name__)

#: Fix branches are named from the PR number so a re-run updates the same branch.
FIX_BRANCH_TEMPLATE = "fix/blast-radius-{pr_number}"

#: GitHub Enterprise sets this; github.com Actions set it to the public API.
API_ROOT: Final[str] = os.environ.get("GITHUB_API_URL", "https://api.github.com")

_PAGE_SIZE: Final[int] = 100
_MAX_PAGES: Final[int] = 10
_TIMEOUT_SECONDS: Final[int] = 30

#: Committer identity for the fix branch. Passed per-command rather than
#: configured, because a CI runner has no git identity of its own and because
#: the author of this commit is a tool, not whoever triggered the job.
_COMMITTER: Final[tuple[str, ...]] = (
    "-c",
    "user.name=blast-radius",
    "-c",
    "user.email=blast-radius@users.noreply.github.com",
)


class PublishError(BlastRadiusError):
    """A publish step could not complete."""


class Transport(Protocol):
    """The seam between this module and the network."""

    def __call__(
        self, method: str, url: str, token: str, body: dict[str, Any] | None = None
    ) -> tuple[int, Any]:
        """Return (status, decoded JSON body)."""


def branch_name_for(pr_number: int) -> str:
    """Return the fix branch name for a pull request."""
    return FIX_BRANCH_TEMPLATE.format(pr_number=pr_number)


# --------------------------------------------------------------------------
# transport
# --------------------------------------------------------------------------


def http(method: str, url: str, token: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
    """Default transport: one GitHub REST call over urllib."""
    if not url.startswith("https://"):
        msg = f"refusing to call a non-https URL: {url}"
        raise PublishError(msg)

    payload = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=payload, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    if payload is not None:
        request.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        return exc.code, {"message": detail}


# --------------------------------------------------------------------------
# the comment
# --------------------------------------------------------------------------


def _identity(token: str, transport: Transport) -> str | None:
    """Who this token posts as, when the token is allowed to say.

    `GITHUB_TOKEN` in Actions cannot read `/user` — it posts as
    `github-actions[bot]` and is not a user at all. That is not an error; it
    means comment selection falls back to the marker, which is what the marker
    is for.
    """
    status, body = transport("GET", f"{API_ROOT}/user", token)
    if status == 200 and isinstance(body, dict):
        login = body.get("login")
        return str(login) if login else None
    return None


def _existing_comment(
    repo: str, pr_number: int, token: str, transport: Transport
) -> dict[str, Any] | None:
    """Find this tool's previous comment, or None."""
    login = _identity(token, transport)

    for page in range(1, _MAX_PAGES + 1):
        url = (
            f"{API_ROOT}/repos/{repo}/issues/{pr_number}/comments?per_page={_PAGE_SIZE}&page={page}"
        )
        status, body = transport("GET", url, token)
        if status != 200 or not isinstance(body, list):
            msg = f"could not list comments on {repo}#{pr_number}: HTTP {status}"
            raise PublishError(msg)

        for comment in body:
            if COMMENT_MARKER not in (comment.get("body") or ""):
                continue
            author = (comment.get("user") or {}).get("login")
            if login is None or author == login:
                return dict(comment)

        if len(body) < _PAGE_SIZE:
            return None
    return None


def upsert_comment(
    repo: str,
    pr_number: int,
    body: str,
    token: str,
    transport: Transport | None = None,
) -> str | None:
    """Post the review comment, replacing this tool's previous one.

    Finds the existing comment by `COMMENT_MARKER` and PATCHes it; only POSTs
    when there is none. Without that, every push adds a comment, the pull
    request fills with them, and the tool gets muted within a week — which is
    the same outcome as not running it.

    Returns the comment URL, or **None** when the comment could not be posted.
    A review that could not be published is worth a warning; it is not worth
    failing a merge that the analysis found harmless. Nothing here raises to the
    caller for a network reason.
    """
    send = transport or http
    try:
        existing = _existing_comment(repo, pr_number, token, send)
        if existing is not None:
            url = f"{API_ROOT}/repos/{repo}/issues/comments/{existing['id']}"
            status, response = send("PATCH", url, token, {"body": body})
            expected = 200
        else:
            url = f"{API_ROOT}/repos/{repo}/issues/{pr_number}/comments"
            status, response = send("POST", url, token, {"body": body})
            expected = 201

        if status != expected:
            logger.warning(
                "could not publish the review comment on %s#%s: HTTP %s", repo, pr_number, status
            )
            return None
    except (PublishError, OSError) as exc:
        logger.warning("could not publish the review comment on %s#%s: %s", repo, pr_number, exc)
        return None

    return str(response.get("html_url")) if isinstance(response, dict) else None


# --------------------------------------------------------------------------
# the fix branch
# --------------------------------------------------------------------------


def _safe_target(target_repo_path: str) -> Path:
    """Reject a fix path that would write outside the repository.

    `target_repo_path` describes where model-generated code should land. It is
    the one field in the report whose value decides which file gets written, so
    it is checked rather than trusted.
    """
    path = Path(target_repo_path)
    if path.is_absolute() or ".." in path.parts:
        msg = f"refusing a fix path that escapes the repository: {target_repo_path}"
        raise PublishError(msg)
    return path


def _commit_message(report: ImpactReport, fixes: list[GeneratedFix]) -> str:
    columns = sorted(
        {
            f"{impact.change.dbt_model}.{impact.change.column}"
            for impact in report.column_impacts
            for fix in fixes
            if impact.change_id in fix.change_ids
        }
    )
    return (
        f"fix: update downstream models for {', '.join(columns)}\n"
        "\n"
        "Generated by blast-radius in response to a schema change in "
        f"#{report.change_set_ref.pull_request.number}.\n"
        "\n"
        "The contents of these files were written by a language model and then "
        "compiled: every file in this commit passed `dbt compile`. Candidates "
        "that did not compile are in the review comment and are deliberately "
        "not here. Compiling is not reviewing — read them.\n"
    )


def push_fix_branch(
    repo: str,
    pr_number: int,
    fixes_dir: Path,
    report: ImpactReport,
    token: str,
    repo_dir: Path | None = None,
) -> str | None:
    """Commit the compile-verified fixes to `fix/blast-radius-<pr>` and push.

    Only fixes whose `validation.passed` is true are committed. A fix that does
    not compile belongs in the comment as a suggestion, never on a branch where
    someone might merge it.

    The branch is cut from the pull request's head SHA, not from the default
    branch, so it applies to the change under review. Work happens in a
    temporary worktree, so the Action's own checkout is never disturbed.

    Force-push is acceptable on this branch and only this branch, because it is
    owned entirely by the tool: the refspec is built from `branch_name_for`, so
    there is no code path that can push anywhere else.

    Returns the branch name, or None when there was nothing to push.
    """
    verified = [fix for fix in report.generated_fixes if fix.validation.passed]
    if not verified:
        return None

    root = (repo_dir or Path.cwd()).resolve()
    branch = branch_name_for(pr_number)
    head_sha = report.change_set_ref.pull_request.head_sha

    with tempfile.TemporaryDirectory(prefix="blast-radius-fix-") as tmp:
        tree = Path(tmp) / "tree"
        run_git(["worktree", "add", "--detach", str(tree), head_sha], root)
        try:
            for fix in verified:
                source = fixes_dir / fix.path
                if not source.is_file():
                    msg = f"fix {fix.id} is missing from {fixes_dir}"
                    raise PublishError(msg)
                destination = tree / _safe_target(fix.target_repo_path)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)

            run_git(["add", "-A"], tree)
            if not run_git(["status", "--porcelain"], tree).strip():
                return None

            run_git([*_COMMITTER, "commit", "-q", "-m", _commit_message(report, verified)], tree)
            run_git(
                [
                    "push",
                    "--force",
                    _push_url(repo, token),
                    f"HEAD:refs/heads/{branch}",
                ],
                tree,
            )
        finally:
            run_git(["worktree", "remove", "--force", str(tree)], root)

    return branch


def _push_url(repo: str, token: str) -> str:
    """Authenticated remote for the push.

    Built here rather than configured as a remote so the token never lands in
    `.git/config`, where it would outlive the job.
    """
    if not token:
        msg = "no token: refusing to push the fix branch"
        raise PublishError(msg)
    return f"https://x-access-token:{token}@github.com/{repo}.git"


__all__ = [
    "API_ROOT",
    "FIX_BRANCH_TEMPLATE",
    "GitError",
    "PublishError",
    "Transport",
    "branch_name_for",
    "http",
    "push_fix_branch",
    "upsert_comment",
]
