"""Build the write-back record from a finished report.

Real implementation: this is a pure projection of an `ImpactReport` onto the
`WritebackRecord` contract. It deliberately copies severity rather than
recomputing it, so the number in DataHub and the number in the PR comment can
never disagree.
"""

from __future__ import annotations

from contracts.models import (
    ImpactReport,
    WritebackColumn,
    WritebackRecord,
    WritebackSeverity,
    WritebackStatus,
)


def build_record(
    report: ImpactReport,
    detected_at: str,
    status: WritebackStatus = "detected",
    report_url: str | None = None,
    fix_branch: str | None = None,
    supersedes: str | None = None,
) -> WritebackRecord:
    """Project a report onto the record that gets written into DataHub.

    `detected_at` is a parameter rather than `datetime.now()` so that the
    function stays pure and testable; the caller owns the clock.
    """
    pr = report.change_set_ref.pull_request

    columns = tuple(
        WritebackColumn(
            dataset_urn=impact.change.dataset_urn,
            column=impact.change.column,
            change_kind=impact.change.change_kind,
            severity_level=impact.severity.level,
            severity_score=impact.severity.score,
            downstream_count=len(impact.downstream),
            new_name=None,
        )
        for impact in report.column_impacts
    )

    # Deduplicated and sorted so that two runs over the same PR produce the same
    # bytes, which makes the record diffable in DataHub's own history.
    downstream_urns = tuple(
        sorted({entity.urn for impact in report.column_impacts for entity in impact.downstream})
    )

    return WritebackRecord(
        pr_url=pr.url or f"https://github.com/{pr.repo}/pull/{pr.number}",
        repo=pr.repo,
        pr_number=pr.number,
        changed_columns=columns,
        severity=WritebackSeverity(
            score=report.overall_severity.score,
            level=report.overall_severity.level,
            rule_version=report.overall_severity.rule_version,
        ),
        detected_at=detected_at,
        status=status,
        downstream_entity_count=len(downstream_urns),
        downstream_urns=downstream_urns,
        report_url=report_url,
        fix_branch=fix_branch,
        tool=report.tool,
        supersedes=supersedes,
    )
