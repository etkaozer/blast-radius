"""Publish the review: post the comment, push the fixes.

Two side effects on someone else's repository, so both are written to be safe to
re-run. A CI job that posts a new comment on every push turns a useful tool into
a reason to disable the tool.
"""

from __future__ import annotations

from pathlib import Path

from contracts.errors import OWNER_B, StubNotImplementedError
from contracts.models import ImpactReport

_T = "ci.publish.github"

#: Fix branches are named from the PR number so a re-run updates the same branch.
FIX_BRANCH_TEMPLATE = "fix/blast-radius-{pr_number}"


def branch_name_for(pr_number: int) -> str:
    """Return the fix branch name for a pull request."""
    return FIX_BRANCH_TEMPLATE.format(pr_number=pr_number)


def upsert_comment(repo: str, pr_number: int, body: str, token: str) -> str:
    """Post the review comment, replacing this tool's previous one.

    Contract:

    - Find an existing comment containing `ci.render.markdown.COMMENT_MARKER`
      authored by this token's identity, and PATCH it. Only POST when none
      exists.
    - Return the comment URL, which goes into `WritebackRecord.report_url` so
      the DataHub record links back to the review.
    - Never fail the CI job on a comment failure alone. A review that could not
      be posted is worth surfacing as a warning; it is not worth blocking a
      merge that the analysis found harmless.
    """
    raise StubNotImplementedError(
        f"{_T}.upsert_comment", OWNER_B, "idempotent PR comment upsert keyed on COMMENT_MARKER"
    )


def push_fix_branch(
    repo: str,
    pr_number: int,
    fixes_dir: Path,
    report: ImpactReport,
    token: str,
) -> str | None:
    """Commit the generated fixes to `fix/blast-radius-<pr>` and push.

    Contract:

    - Only commit fixes whose `validation.passed` is true. A fix that does not
      compile belongs in the comment as a suggestion, never on a branch where
      someone might merge it.
    - Branch from the PR's head SHA, not from the default branch, so the fix
      applies to the change under review.
    - Force-push is acceptable on this branch and only this branch, because it
      is owned entirely by the tool. Never push to the PR's own branch.
    - Commit message states which columns it responds to and that the contents
      were model-generated and compiler-verified.
    - Return the branch name, or None when there was nothing to push.
    """
    raise StubNotImplementedError(
        f"{_T}.push_fix_branch",
        OWNER_B,
        "commit only compile-verified fixes onto fix/blast-radius-<pr>, branched from head",
    )
