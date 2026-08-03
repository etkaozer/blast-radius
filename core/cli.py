"""`blast-radius` command line interface.

Three commands:

* `analyze`  — run the pipeline over a change set and write an impact report
* `doctor`   — verify the DataHub read and write paths before depending on them
* `stubs`    — list what is still unimplemented, grouped by owner

Exit codes are meaningful because CI reads them:

| code | meaning |
| ---- | ------- |
| 0 | success |
| 1 | bad input, bad configuration, or a contract violation |
| 2 | ran, but a capability is missing (doctor) |
| 3 | halted on an unimplemented stage |
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from contracts.loader import ContractViolation, dump
from core.config import Settings
from core.errors import BlastRadiusError
from core.pipeline import STAGES, AnalyzeRequest, Progress, Stage, run_analysis
from core.stubs import StubRef, find_stubs, group_by_owner
from core.version import VERSION
from core.writeback.doctor import exit_code_for, run_checks

EXIT_OK = 0
EXIT_BAD_INPUT = 1
EXIT_DEGRADED = 2
EXIT_STUB = 3


def _err(message: str) -> None:
    click.echo(message, err=True)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(VERSION, prog_name="blast-radius")
def main() -> None:
    """Review data pull requests for breaking schema changes, grounded in DataHub."""


@main.command()
@click.option(
    "--change-set",
    "change_set_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="ChangeSet JSON produced by ci/diff (see contracts/change_set.schema.json).",
)
@click.option(
    "--out",
    "out_path",
    required=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Where to write the ImpactReport JSON.",
)
@click.option(
    "--fixes-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Directory for generated fix files. Omit to skip fix generation.",
)
@click.option(
    "--no-agent", is_flag=True, help="Skip the model. Severity and lineage are unaffected."
)
@click.option(
    "--datahub-mode",
    type=click.Choice(["mcp", "sdk"]),
    default=None,
    help="Override BLAST_RADIUS_DATAHUB_MODE for this run.",
)
def analyze(
    change_set_path: Path,
    out_path: Path,
    fixes_dir: Path | None,
    no_agent: bool,
    datahub_mode: str | None,
) -> None:
    """Analyze a change set and write an impact report."""
    try:
        settings = Settings.from_env()
    except BlastRadiusError as exc:
        _err(f"configuration error: {exc}")
        sys.exit(EXIT_BAD_INPUT)

    if datahub_mode is not None:
        settings = settings.with_overrides(datahub_mode=datahub_mode)

    request = AnalyzeRequest(
        change_set_path=change_set_path,
        out_path=out_path,
        fixes_dir=fixes_dir,
        settings=settings,
        use_agent=not no_agent,
        generate_fixes=fixes_dir is not None,
    )

    click.echo(f"blast-radius {VERSION} · analyze")
    click.echo(f"change set: {change_set_path}")
    click.echo()

    progress = Progress()

    def show(stage: Stage, detail: str) -> None:
        index = STAGES.index(stage) + 1
        click.echo(f"  [{index}/{len(STAGES)}] {stage.title:<34} ok   {detail}")

    try:
        report = run_analysis(request, progress, report_stage=show)
    except ContractViolation as exc:
        click.echo()
        _err(f"✗ {exc}")
        sys.exit(EXIT_BAD_INPUT)
    except NotImplementedError as exc:
        _report_halt(progress, exc)
        sys.exit(EXIT_STUB)
    except BlastRadiusError as exc:
        click.echo()
        _err(f"✗ {exc}")
        sys.exit(EXIT_BAD_INPUT)

    try:
        dump(report, out_path, "impact_report")
    except ContractViolation as exc:
        _err(f"✗ the generated report does not satisfy its own contract:\n{exc}")
        sys.exit(EXIT_BAD_INPUT)

    click.echo()
    click.echo(f"✓ {report.overall_severity.level} ({report.overall_severity.score}) → {out_path}")


def _report_halt(progress: Progress, exc: NotImplementedError) -> None:
    """Explain where the pipeline stopped and what remains on this path."""
    stage = progress.failed_stage
    click.echo()
    if stage is None:
        _err(f"✗ halted: {exc}")
        return

    index = STAGES.index(stage) + 1
    click.echo(f"  [{index}/{len(STAGES)}] {stage.title:<34} NOT IMPLEMENTED")
    click.echo()
    _err(f"✗ pipeline halted at stage {index}/{len(STAGES)}: {stage.title}")
    _err("")
    for line in str(exc).splitlines():
        _err(f"  {line}")
    _err("")

    remaining_modules = {s.module for s in STAGES[index - 1 :]}
    stubs = find_stubs()
    on_path = [s for s in stubs if s.module in remaining_modules]
    if on_path:
        _err("Remaining work on this path:")
        for owner, group in group_by_owner(tuple(on_path)).items():
            _err(f"  owner {owner}")
            for stub in group:
                _err(f"    {stub.location:<40} {stub.qualname}")
        _err("")
    elsewhere = len(stubs) - len(on_path)
    if elsewhere:
        _err(f"{elsewhere} further stub(s) elsewhere. Run `blast-radius stubs` for the inventory.")


@main.command()
def doctor() -> None:
    """Verify that the DataHub read and write paths work."""
    try:
        settings = Settings.from_env()
    except BlastRadiusError as exc:
        _err(f"configuration error: {exc}")
        sys.exit(EXIT_BAD_INPUT)

    click.echo(f"blast-radius {VERSION} · doctor")
    click.echo()
    results = run_checks(settings)
    for result in results:
        owner = f"  [owner {result.owner}]" if result.owner else ""
        click.echo(f"  {result.symbol} {result.name:<20} {result.detail}{owner}")

    code = exit_code_for(results)
    click.echo()
    if code == EXIT_OK:
        click.echo("✓ read and write paths verified")
    elif code == EXIT_DEGRADED:
        click.echo("· some checks are not implemented yet; do not depend on the write path")
    else:
        click.echo("✗ environment is not usable; fix the failures above")
    sys.exit(code)


@main.command()
@click.option("--owner", default=None, help="Filter by owner label, e.g. 'A' or 'B'.")
def stubs(owner: str | None) -> None:
    """List every unimplemented stub, grouped by owner."""
    found: tuple[StubRef, ...] = find_stubs()
    if owner:
        needle = owner.lower()
        found = tuple(s for s in found if needle in s.owner_label.lower())

    if not found:
        click.echo("No stubs found. Either the work is done or the scanner is broken.")
        return

    click.echo(f"{len(found)} stub(s) remaining")
    for label, group in group_by_owner(found).items():
        click.echo()
        click.echo(f"owner {label} — {len(group)}")
        for stub in group:
            click.echo(f"  {stub.location:<44} {stub.qualname}")
            click.echo(f"  {'':<44} {stub.contract}")


if __name__ == "__main__":  # pragma: no cover
    main()
