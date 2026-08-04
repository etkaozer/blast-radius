"""Render an ImpactReport as a pull-request comment.

The comment is the product for most readers: they will never open the JSON. It
has about fifteen seconds to answer three questions — how bad, what breaks, who
needs to know — before the reader scrolls past.

Rendering rules that are contract, not taste:

* Never render `explanation.text` without its model-generated label. The schema
  makes the label a constant so the renderer cannot lose it.
* Never render a generated fix as if it were verified when
  `validation.passed` is false. Failed fixes go in a separate, collapsed
  section labelled as not compiling.
* Always render `degradations`. A report that could not read query usage must
  not look like a report that found no usage.
* Untrusted text is quoted, never interpolated: a description containing
  markdown, HTML or an @mention must not be able to alter the comment's
  structure or ping anyone.
"""

from __future__ import annotations

import re
from typing import Final

from contracts.models import (
    ColumnImpact,
    Degradation,
    GeneratedFix,
    ImpactReport,
    Owner,
    Severity,
    SeverityLevel,
    UntrustedFinding,
)

#: Marker used to find and update this tool's previous comment instead of
#: posting a new one on every push.
COMMENT_MARKER: Final[str] = "<!-- blast-radius:impact-report -->"

SEVERITY_BADGE: Final[dict[SeverityLevel, str]] = {
    "critical": "🔴 critical",
    "high": "🟠 high",
    "medium": "🟡 medium",
    "low": "⚪ low",
}

#: How many factors of the seven to show inline. The full breakdown is in the
#: report; the table has to stay readable.
_TOP_FACTORS: Final[int] = 3

#: A handle we are willing to turn into a real @mention. Ownership comes from
#: DataHub, which is a writable system: a "handle" of `@everyone lol` would
#: otherwise ping an organisation from a field nobody thought of as free text.
_SAFE_HANDLE = re.compile(r"^@[A-Za-z0-9][A-Za-z0-9-]{0,38}$")

#: Zero-width space. Inserted after an `@` so the text reads identically to a
#: human and means nothing to GitHub's mention parser.
_ZWSP: Final[str] = "​"

_ENTITY_ICON: Final[dict[str, str]] = {
    "dashboard": "📊",
    "chart": "📈",
    "mlFeature": "🧠",
    "mlModel": "🧠",
    "mlPrimaryKey": "🧠",
    "dataJob": "⚙️",
    "dataFlow": "⚙️",
    "notebook": "📓",
    "dataset": "🗄️",
}


# --------------------------------------------------------------------------
# quoting
# --------------------------------------------------------------------------


def quote_untrusted(text: str) -> str:
    """Quote untrusted text so it cannot alter the comment or mention anyone.

    Three things happen, and each one is reversible by eye:

    * `&`, `<` and `>` are HTML-escaped, so markup in a description renders as
      the characters someone typed rather than as markup — including a copy of
      `COMMENT_MARKER`, which would otherwise let a description confuse the
      publisher into updating the wrong comment;
    * a zero-width space follows every `@`, which stops GitHub resolving a
      mention while leaving the text visually identical;
    * backticks are backslash-escaped and every line is prefixed with `> `, so
      the passage cannot close a code fence or escape its blockquote.

    The content itself is preserved: a reader sees exactly what was written,
    which is the point — a description that argues with the lineage graph is
    the most interesting thing in the diff. Escaping is for rendering safety
    only; `untrusted_text.value` in the report stays verbatim.
    """
    escaped = (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("`", "\\`")
        .replace("@", f"@{_ZWSP}")
    )
    return "\n".join(f"> {line}" if line else ">" for line in escaped.splitlines())


def _mention(owner: Owner) -> str:
    """Render an owner, @-mentioning them only if the handle is safely shaped."""
    if owner.handle and _SAFE_HANDLE.match(owner.handle):
        return f"{owner.handle} ({owner.display_name})"
    return f"`{owner.display_name}`"


def _urn_label(urn: str) -> str | None:
    """Return the human-readable middle of a DataHub URN, or None if it has none.

    `urn:li:dataset:(urn:li:dataPlatform:snowflake,analytics.dim_customers,PROD)`
    → `analytics.dim_customers`. Entity types other than dataset carry fewer
    segments, so the last one before the closing paren is the name. Anything
    that does not parse returns None rather than a fragment of a URN: half a
    URN in a comment reads as a bug in the tool.
    """
    if "(" not in urn or not urn.endswith(")"):
        return None
    parts = [part for part in urn[urn.index("(") + 1 : -1].split(",") if part]
    if len(parts) >= 3:
        return parts[-2]
    if len(parts) == 2:
        return parts[-1]
    return None


# --------------------------------------------------------------------------
# severity
# --------------------------------------------------------------------------


def _dominant_factors(severity: Severity, limit: int = _TOP_FACTORS) -> str:
    """Name the factors that actually moved the score, largest first."""
    ranked = sorted(severity.factors, key=lambda f: (-f.contribution, f.name))
    shown = [f for f in ranked[:limit] if f.contribution > 0]
    if not shown:
        return "no factor contributed"
    return ", ".join(f"{f.name} {f.raw_value} → +{f.contribution:g}" for f in shown)


def render_severity_table(report: ImpactReport) -> str:
    """Render the per-column severity table with its factor breakdown.

    Shows `score`, `level` and the highest-contributing factors with their raw
    values, so a reviewer can re-derive the number by hand rather than having to
    trust it. The rule version is named because the weights are a judgement call
    and a reader is entitled to disagree with a specific one.
    """
    rows = [
        "| Column | Change | Severity | What drove it |",
        "| --- | --- | --- | --- |",
    ]
    for impact in report.column_impacts:
        change = impact.change
        rows.append(
            f"| `{change.dataset_name}.{change.column}` "
            f"| {change.change_kind} "
            f"| {SEVERITY_BADGE[impact.severity.level]} {impact.severity.score:g} "
            f"| {_dominant_factors(impact.severity)} |"
        )
    rows.append("")
    rows.append(
        f"Scored by `{report.overall_severity.rule_version}`, "
        f"computed_by `{report.overall_severity.computed_by}`. "
        "Every factor is a graph fact; no model input reaches this number."
    )
    return "\n".join(rows)


# --------------------------------------------------------------------------
# sections
# --------------------------------------------------------------------------


def _verdict(report: ImpactReport) -> list[str]:
    columns = len(report.column_impacts)
    entities = len({e.urn for impact in report.column_impacts for e in impact.downstream})
    severity = report.overall_severity
    reach = "1 downstream entity" if entities == 1 else f"{entities} downstream entities"
    return [
        COMMENT_MARKER,
        f"## {SEVERITY_BADGE[severity.level]} — blast radius {severity.score:g}/100",
        "",
        f"{_count(columns, 'changed column')} reaching {reach}.",
    ]


def _downstream(impact: ColumnImpact) -> list[str]:
    if not impact.downstream:
        return [f"No downstream consumer reaches `{impact.change.column}`."]

    lines: list[str] = []
    for entity in sorted(impact.downstream, key=lambda e: (e.hop_distance, e.name)):
        icon = _ENTITY_ICON.get(entity.entity_type, "•")
        # Pointy-bracket destination: a DataHub dataset URL contains the URN, and
        # the URN contains parentheses and commas that would otherwise end the
        # link early.
        name = f"[{entity.name}](<{entity.url}>)" if entity.url else f"`{entity.name}`"
        via = f" via `{entity.via_column}`" if entity.via_column else ""
        transformation = entity.path[-1].transformation.type
        lines.append(
            f"- {icon} {name} — {entity.hop_distance} hop"
            f"{'s' if entity.hop_distance != 1 else ''}{via}, {transformation}"
        )
    return lines


def _governance(impact: ColumnImpact) -> list[str]:
    lines: list[str] = []
    usage = impact.query_usage
    if usage.source == "unavailable":
        lines.append("- Query usage: **not available** — this is not the same as unused.")
    else:
        users = f" by {usage.distinct_user_count} users" if usage.distinct_user_count else ""
        lines.append(
            f"- Queried {usage.query_count} times{users} in the last {usage.window_days} days."
        )

    for contract in impact.data_contracts:
        touches = " naming this column" if contract.references_changed_column else ""
        name = contract.name or contract.urn
        lines.append(f"- Data contract `{name}` ({contract.state}){touches}.")
    for assertion in impact.assertions:
        touches = " naming this column" if assertion.references_changed_column else ""
        lines.append(f"- {assertion.assertion_type} assertion{touches}.")
    return lines


def _explanation(impact: ColumnImpact) -> list[str]:
    if impact.explanation is None:
        return []
    explanation = impact.explanation
    model = f" ({explanation.model})" if explanation.model else ""
    return [
        "",
        f"<sub>{explanation.disclaimer}{model}</sub>",
        "",
        explanation.text,
    ]


def _impact_section(impact: ColumnImpact) -> list[str]:
    change = impact.change
    lines = [
        f"### `{change.column}` — {change.change_kind} "
        f"({SEVERITY_BADGE[impact.severity.level]} {impact.severity.score:g})",
        "",
        f"`{change.dataset_name}` · change `{impact.change_id}`",
        "",
    ]
    lines.extend(_downstream(impact))
    lines.append("")
    lines.extend(_governance(impact))
    lines.extend(_explanation(impact))
    return lines


def _owners(report: ImpactReport) -> list[str]:
    seen: dict[str, Owner] = {}
    for impact in report.column_impacts:
        for owner in impact.owners_to_notify:
            seen.setdefault(owner.urn, owner)
    if not seen:
        return []

    lines = ["## Owners to notify", ""]
    for owner in sorted(seen.values(), key=lambda o: o.urn):
        role = f" — {owner.ownership_type}" if owner.ownership_type else ""
        label = _urn_label(owner.for_urn) if owner.for_urn else None
        scope = f" of `{label}`" if label else ""
        lines.append(f"- {_mention(owner)}{role}{scope}")
    return lines


def _findings(report: ImpactReport) -> list[str]:
    findings: list[UntrustedFinding] = [
        finding for impact in report.column_impacts for finding in impact.untrusted_findings
    ]
    if not findings:
        return []

    lines = [
        "## Text addressed at the reviewer",
        "",
        "Free text in this pull request reads as an instruction to an automated "
        "reviewer. It is reproduced below because you should see it, and it "
        "**did not affect the severity score** — the score was computed from "
        "lineage, usage and governance facts before any of this text was read.",
    ]
    for finding in findings:
        lines.extend(
            [
                "",
                f"**{finding.pattern_id}** · confidence {finding.confidence} · "
                f"effect on severity: **{finding.effect_on_severity}**",
                "",
                quote_untrusted(finding.excerpt),
                "",
                f"<sub>{finding.rationale} Detected by a heuristic, which will "
                "miss a paraphrase — the defence is that severity is computed "
                "before this text is read, not that this detector caught it.</sub>",
            ]
        )
    return lines


def _count(number: int, noun: str) -> str:
    """`1 candidate`, `2 candidates` — the comment is read by people."""
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _fix_line(fix: GeneratedFix) -> str:
    attempts = (
        f", {_count(fix.generated_by.attempts, 'attempt')}"
        if fix.generated_by and fix.generated_by.attempts
        else ""
    )
    return f"- `{fix.target_repo_path}` — for {', '.join(fix.change_ids)}{attempts}"


def _fixes(report: ImpactReport) -> list[str]:
    if not report.generated_fixes:
        return []

    compiled = [fix for fix in report.generated_fixes if fix.validation.passed]
    failed = [fix for fix in report.generated_fixes if not fix.validation.passed]

    lines = ["## Candidate fixes", ""]
    if compiled:
        each = "each verified" if len(compiled) > 1 else "verified"
        lines.append(
            f"{_count(len(compiled), 'model-generated file')}, {each} by "
            f"`{compiled[0].validation.tool} compile` before being called a fix:"
        )
        lines.append("")
        lines.extend(_fix_line(fix) for fix in compiled)
        lines.append("")
        lines.append("<sub>Model-generated code. Read it before merging.</sub>")

    if failed:
        lines.extend(
            [
                "",
                "<details>",
                f"<summary>{_count(len(failed), 'candidate')} <b>did not compile</b> — "
                "suggestions only, never pushed to the fix branch</summary>",
                "",
            ]
        )
        for fix in failed:
            lines.append(f"**`{fix.target_repo_path}`** — not verified, `{fix.validation.command}`")
            if fix.validation.output_excerpt:
                lines.extend(["", quote_untrusted(fix.validation.output_excerpt)])
            lines.append("")
        lines.append("</details>")
    return lines


def _degradations(degradations: tuple[Degradation, ...]) -> list[str]:
    if not degradations:
        return []
    lines = [
        "## What could not be measured",
        "",
        "Absence of data is not absence of impact. These capabilities were "
        "unavailable, so the score above is a floor rather than an estimate.",
        "",
    ]
    for degradation in degradations:
        consequence = f" {degradation.consequence}" if degradation.consequence else ""
        lines.append(f"- **{degradation.capability}** — {degradation.reason}.{consequence}")
    return lines


def _footer(report: ImpactReport) -> list[str]:
    context = ""
    if report.datahub is not None:
        hops = report.datahub.lineage_max_hops
        depth = f", {hops} hops" if hops else ""
        context = f" · DataHub via `{report.datahub.access_path}`{depth}"
    return [
        "---",
        "",
        f"<sub>`blast-radius` {report.tool.version} · severity rules "
        f"`{report.tool.severity_rule_version}`{context} · "
        f"report generated {report.generated_at} · "
        f"PR #{report.change_set_ref.pull_request.number}</sub>",
    ]


# --------------------------------------------------------------------------
# the comment
# --------------------------------------------------------------------------


def render_comment(report: ImpactReport) -> str:
    """Render the whole PR comment.

    Section order is reading order: the verdict, then the table that justifies
    it, then what breaks, then who to tell, then the text that tried to talk the
    reviewer out of it, then the fixes, then what could not be measured.

    Byte-for-byte deterministic. Nothing here reads the clock or the
    environment: the same report renders to the same markdown, which is what
    makes updating the existing comment instead of posting a new one safe.
    """
    blocks: list[list[str]] = [
        _verdict(report),
        ["## Severity", "", render_severity_table(report)],
        ["## What this reaches"],
    ]
    for impact in report.column_impacts:
        blocks.append(_impact_section(impact))
    blocks.extend(
        [
            _owners(report),
            _findings(report),
            _fixes(report),
            _degradations(report.degradations),
            _footer(report),
        ]
    )
    return "\n\n".join("\n".join(block) for block in blocks if block) + "\n"
