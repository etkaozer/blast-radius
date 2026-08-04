"""Publishing, tested without a network and without a GitHub.

The transport is a parameter, so the comment rules are tested by recording calls
against a fake. The fix branch is tested against a real repository in a temp
directory, because the rules that matter there — branch from the head SHA, only
compiled fixes, never push anywhere but the fix branch — are properties of what
git ends up containing, not of which functions we called.

Only `contracts` and `ci` are imported. Nothing here touches `core/`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from ci.publish.github import (
    PublishError,
    branch_name_for,
    push_fix_branch,
    upsert_comment,
)
from ci.render.markdown import COMMENT_MARKER
from contracts.loader import fixture_dir, load_impact_report
from contracts.models import ImpactReport

#: Not a credential. The transport asserts it arrives unchanged.
TOKEN = "ghs-not-a-real-token"


class FakeGitHub:
    """Records calls and answers them from a script."""

    def __init__(
        self,
        comments: list[dict[str, Any]] | None = None,
        login: str | None = "github-actions[bot]",
        write_status: int | None = None,
    ) -> None:
        self.comments = comments or []
        self.login = login
        self.write_status = write_status
        self.calls: list[tuple[str, str]] = []

    def __call__(
        self, method: str, url: str, token: str, body: dict[str, Any] | None = None
    ) -> tuple[int, Any]:
        self.calls.append((method, url))
        assert token == TOKEN

        if url.endswith("/user"):
            return (200, {"login": self.login}) if self.login else (403, {})
        if method == "GET":
            return 200, self.comments
        if method == "PATCH":
            return self.write_status or 200, {"html_url": "https://github.com/c/1#patched"}
        if method == "POST":
            return self.write_status or 201, {"html_url": "https://github.com/c/2#posted"}
        raise AssertionError(method)

    def methods(self) -> list[str]:
        return [method for method, url in self.calls if not url.endswith("/user")]


def comment(identifier: int, body: str, login: str = "github-actions[bot]") -> dict[str, Any]:
    return {"id": identifier, "body": body, "user": {"login": login}}


def report(name: str = "02_removal_contract") -> ImpactReport:
    return load_impact_report(fixture_dir(name) / "expected_impact_report.json")


# --------------------------------------------------------------------------
# the comment
# --------------------------------------------------------------------------


def test_the_first_run_posts() -> None:
    api = FakeGitHub(comments=[comment(1, "unrelated review note")])
    url = upsert_comment("acme/analytics", 128, "body", TOKEN, api)
    assert api.methods() == ["GET", "POST"]
    assert url == "https://github.com/c/2#posted"


def test_the_second_run_edits_instead_of_posting() -> None:
    """Every push posting a new comment is how a useful tool gets muted."""
    api = FakeGitHub(comments=[comment(7, f"{COMMENT_MARKER}\nold body")])
    url = upsert_comment("acme/analytics", 128, "new body", TOKEN, api)
    assert api.methods() == ["GET", "PATCH"]
    assert "issues/comments/7" in api.calls[-1][1]
    assert url == "https://github.com/c/1#patched"


def test_a_marker_in_someone_elses_comment_is_not_edited() -> None:
    """A human can quote our comment. Editing theirs would be rude and wrong."""
    api = FakeGitHub(comments=[comment(9, f"look: {COMMENT_MARKER}", login="a-human")])
    upsert_comment("acme/analytics", 128, "body", TOKEN, api)
    assert api.methods() == ["GET", "POST"]


def test_a_token_that_cannot_read_its_own_identity_falls_back_to_the_marker() -> None:
    """GITHUB_TOKEN in Actions cannot read /user; it is not a user. That is not
    an error — it is what the marker is for."""
    api = FakeGitHub(comments=[comment(7, COMMENT_MARKER, login="whoever")], login=None)
    upsert_comment("acme/analytics", 128, "body", TOKEN, api)
    assert api.methods() == ["GET", "PATCH"]


def test_a_failed_post_returns_none_rather_than_raising() -> None:
    """A review that could not be posted is worth a warning. It is not worth
    failing a merge the analysis found harmless."""
    api = FakeGitHub(write_status=403)
    assert upsert_comment("acme/analytics", 128, "body", TOKEN, api) is None


def test_a_transport_that_explodes_does_not_reach_the_caller() -> None:
    def broken(method: str, url: str, token: str, body: dict[str, Any] | None = None) -> Any:
        raise OSError("connection reset")

    assert upsert_comment("acme/analytics", 128, "body", TOKEN, broken) is None


# --------------------------------------------------------------------------
# the fix branch
# --------------------------------------------------------------------------


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, check=True, text=True
    ).stdout.strip()


@pytest.fixture
def bare(tmp_path: Path) -> Path:
    """A bare repository standing in for GitHub."""
    path = tmp_path / "origin.git"
    path.mkdir()
    git(path, "init", "-q", "--bare", "-b", "main")
    return path


@pytest.fixture
def origin(tmp_path: Path, bare: Path) -> Path:
    """A checkout with one commit, as the Action would have."""
    work = tmp_path / "work"
    work.mkdir()
    git(work, "init", "-q", "-b", "main")
    (work / "models").mkdir()
    (work / "models" / "customer_ltv.sql").write_text("select 1 as id\n", encoding="utf-8")
    git(work, "add", "-A")
    git(work, "-c", "user.name=t", "-c", "user.email=t@e.com", "commit", "-q", "-m", "base")
    git(work, "remote", "add", "origin", str(bare))
    git(work, "push", "-q", "origin", "main")
    return work


@pytest.fixture
def push_to_bare(bare: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Send the push to the local bare repository instead of github.com.

    Only the URL is faked. The commit, the branch and the refspec are the real
    ones, which is the part worth testing.
    """

    def to_bare(repo: str, token: str) -> str:
        return str(bare)

    monkeypatch.setattr("ci.publish.github._push_url", to_bare)


def head_sha(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD")


def at_head(repo: Path) -> ImpactReport:
    """The fixture report, re-pointed at a SHA that exists in `repo`."""
    payload = report().model_dump()
    payload["change_set_ref"]["pull_request"]["head_sha"] = head_sha(repo)
    payload["change_set_ref"]["pull_request"]["base_sha"] = head_sha(repo)
    return ImpactReport.model_validate(payload)


@pytest.fixture
def fixes(tmp_path: Path) -> Path:
    directory = tmp_path / "fixes"
    directory.mkdir()
    (directory / "customer_ltv.sql").write_text("select 2 as id\n", encoding="utf-8")
    (directory / "mart_exec_summary.sql").write_text("select broken\n", encoding="utf-8")
    return directory


def test_only_the_fix_that_compiled_reaches_the_branch(
    origin: Path, bare: Path, fixes: Path, push_to_bare: None
) -> None:
    """Fixture 02 has two fixes and one of them failed `dbt compile`. A fix that
    does not compile must never land where someone could merge it."""
    branch = push_fix_branch("acme/analytics", 214, fixes, at_head(origin), TOKEN, origin)

    assert branch == "fix/blast-radius-214"
    listed = git(bare, "ls-tree", "-r", "--name-only", branch)
    assert "models/marts/customer_ltv.sql" in listed
    assert "mart_exec_summary" not in listed


def test_the_branch_is_cut_from_the_pull_request_head(
    origin: Path, bare: Path, fixes: Path, push_to_bare: None
) -> None:
    """Branching from the default branch would produce a fix that does not apply
    to the change under review."""
    expected = head_sha(origin)
    push_fix_branch("acme/analytics", 214, fixes, at_head(origin), TOKEN, origin)
    assert git(bare, "rev-parse", "fix/blast-radius-214~1") == expected


def test_the_commit_says_the_code_was_generated_and_compiled(
    origin: Path, bare: Path, fixes: Path, push_to_bare: None
) -> None:
    push_fix_branch("acme/analytics", 214, fixes, at_head(origin), TOKEN, origin)
    message = git(bare, "log", "-1", "--format=%B", "fix/blast-radius-214")
    assert "written by a language model" in message
    assert "dbt compile" in message
    assert "dim_customers.customer_lifetime_value" in message


def test_the_committer_is_the_tool_not_whoever_triggered_the_job(
    origin: Path, bare: Path, fixes: Path, push_to_bare: None
) -> None:
    """A CI runner has no git identity of its own, and the author of this commit
    is a tool."""
    push_fix_branch("acme/analytics", 214, fixes, at_head(origin), TOKEN, origin)
    assert git(bare, "log", "-1", "--format=%an", "fix/blast-radius-214") == "blast-radius"


def test_the_working_tree_is_left_alone(origin: Path, fixes: Path, push_to_bare: None) -> None:
    """The Action's checkout is the pull request head. Publishing must not move
    it or dirty it."""
    before = head_sha(origin)
    push_fix_branch("acme/analytics", 214, fixes, at_head(origin), TOKEN, origin)
    assert head_sha(origin) == before
    assert git(origin, "status", "--porcelain") == ""
    assert git(origin, "branch", "--show-current") == "main"


def test_a_report_with_no_verified_fix_pushes_nothing(origin: Path, fixes: Path) -> None:
    payload = at_head(origin).model_dump()
    for fix in payload["generated_fixes"]:
        fix["validation"]["passed"] = False
    without = ImpactReport.model_validate(payload)
    assert push_fix_branch("acme/analytics", 214, fixes, without, TOKEN, origin) is None


def test_a_fix_path_that_escapes_the_repository_is_refused(
    origin: Path, fixes: Path, push_to_bare: None
) -> None:
    """`target_repo_path` decides which file gets written, so it is checked
    rather than trusted."""
    payload = at_head(origin).model_dump()
    payload["generated_fixes"][0]["target_repo_path"] = "../../etc/authorized_keys"
    with pytest.raises(PublishError, match="escapes the repository"):
        push_fix_branch(
            "acme/analytics", 214, fixes, ImpactReport.model_validate(payload), TOKEN, origin
        )


def test_a_missing_fix_file_is_an_error_not_an_empty_commit(origin: Path, tmp_path: Path) -> None:
    empty = tmp_path / "no-fixes"
    empty.mkdir()
    with pytest.raises(PublishError, match="missing"):
        push_fix_branch("acme/analytics", 214, empty, at_head(origin), TOKEN, origin)


# --------------------------------------------------------------------------
# the branch name
# --------------------------------------------------------------------------


@pytest.mark.parametrize("pr_number", [1, 128, 99999])
def test_the_refspec_can_only_ever_name_the_fix_branch(pr_number: int) -> None:
    """Force-push is safe here because there is no code path that can push
    anywhere else: the refspec is built from this one function."""
    name = branch_name_for(pr_number)
    assert name == f"fix/blast-radius-{pr_number}"
    assert name.startswith("fix/blast-radius-")
