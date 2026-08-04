"""`blast-radius-ci` — OWNER B's half of the pipeline.

Kept as a separate entry point from `blast-radius` so that the two halves can be
run, tested and debugged independently, and so neither owner has to edit the
other's CLI to add a flag.

    blast-radius-ci extract --base-sha X --head-sha Y --out change_set.json
    blast-radius-ci render  --report report.json --out comment.md
    blast-radius-ci publish --report report.json --comment comment.md --fixes-dir out/fixes
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from contracts.errors import BlastRadiusError
from contracts.models import PullRequestRef
from contracts.version import VERSION

EXIT_OK = 0
EXIT_BAD_INPUT = 1
EXIT_STUB = 3


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(VERSION, prog_name="blast-radius-ci")
def main() -> None:
    """Diff extraction, comment rendering and publishing for blast-radius."""


@main.command()
@click.option("--repo", required=True, help="owner/name of the data repository.")
@click.option("--pr-number", required=True, type=int)
@click.option("--base-sha", required=True)
@click.option("--head-sha", required=True)
@click.option("--project-dir", type=click.Path(file_okay=False, path_type=Path), default=Path())
@click.option("--out", "out_path", required=True, type=click.Path(dir_okay=False, path_type=Path))
def extract(
    repo: str,
    pr_number: int,
    base_sha: str,
    head_sha: str,
    project_dir: Path,
    out_path: Path,
) -> None:
    """Extract changed columns from the dbt diff into a ChangeSet."""
    from ci.diff.extract import build_change_set
    from ci.diff.git import collect_file_diffs

    try:
        file_diffs = collect_file_diffs(base_sha, head_sha, project_dir=project_dir)
        if not file_diffs:
            click.echo("✓ no dbt model changed; nothing to review")
            sys.exit(EXIT_OK)
        change_set = build_change_set(
            pull_request=_pull_request_ref(repo, pr_number, base_sha, head_sha),
            file_diffs=file_diffs,
            manifest_path=project_dir / "target" / "manifest.json",
        )
    except NotImplementedError as exc:
        _halt(exc)
    except BlastRadiusError as exc:
        click.echo(f"✗ {exc}", err=True)
        sys.exit(EXIT_BAD_INPUT)

    from contracts.loader import dump

    dump(change_set, out_path, "change_set")
    click.echo(f"✓ {len(change_set.column_changes)} column change(s) → {out_path}")


@main.command()
@click.option(
    "--report",
    "report_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option("--out", "out_path", required=True, type=click.Path(dir_okay=False, path_type=Path))
def render(report_path: Path, out_path: Path) -> None:
    """Render an ImpactReport as a markdown pull-request comment."""
    from ci.render.markdown import render_comment
    from contracts.loader import load_impact_report

    report = load_impact_report(report_path)
    try:
        body = render_comment(report)
    except NotImplementedError as exc:
        _halt(exc)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(body, encoding="utf-8")
    click.echo(f"✓ comment → {out_path}")


@main.command()
@click.option(
    "--report",
    "report_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--comment",
    "comment_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option("--fixes-dir", type=click.Path(file_okay=False, path_type=Path), default=None)
@click.option("--token", envvar="GITHUB_TOKEN", required=True)
def publish(report_path: Path, comment_path: Path, fixes_dir: Path | None, token: str) -> None:
    """Post the comment and push the generated fixes."""
    from ci.publish.github import push_fix_branch, upsert_comment
    from contracts.loader import load_impact_report

    report = load_impact_report(report_path)
    pr = report.change_set_ref.pull_request
    try:
        url = upsert_comment(pr.repo, pr.number, comment_path.read_text(encoding="utf-8"), token)
        branch = (
            push_fix_branch(pr.repo, pr.number, fixes_dir, report, token) if fixes_dir else None
        )
    except NotImplementedError as exc:
        _halt(exc)
    except BlastRadiusError as exc:
        click.echo(f"✗ {exc}", err=True)
        sys.exit(EXIT_BAD_INPUT)

    if url:
        click.echo(f"✓ comment: {url}")
    else:
        # Deliberately not an error exit: a review that could not be posted is
        # worth surfacing, but it is not worth failing a merge over.
        click.echo("! the review comment could not be posted; see the log", err=True)
    if branch:
        click.echo(f"✓ fixes pushed to {branch}")


def _pull_request_ref(repo: str, pr_number: int, base_sha: str, head_sha: str) -> PullRequestRef:
    return PullRequestRef(number=pr_number, repo=repo, base_sha=base_sha, head_sha=head_sha)


def _halt(exc: NotImplementedError) -> None:
    click.echo("", err=True)
    click.echo("✗ not implemented yet:", err=True)
    for line in str(exc).splitlines():
        click.echo(f"  {line}", err=True)
    click.echo("", err=True)
    click.echo("Run `blast-radius stubs --owner B` for the full inventory.", err=True)
    sys.exit(EXIT_STUB)


if __name__ == "__main__":  # pragma: no cover
    main()
