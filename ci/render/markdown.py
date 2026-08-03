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

from typing import Final

from contracts.errors import OWNER_B, StubNotImplementedError
from contracts.models import ImpactReport, SeverityLevel

_T = "ci.render.markdown"

#: Marker used to find and update this tool's previous comment instead of
#: posting a new one on every push.
COMMENT_MARKER: Final[str] = "<!-- blast-radius:impact-report -->"

SEVERITY_BADGE: Final[dict[SeverityLevel, str]] = {
    "critical": "🔴 critical",
    "high": "🟠 high",
    "medium": "🟡 medium",
    "low": "⚪ low",
}


def render_comment(report: ImpactReport) -> str:
    """Render the whole PR comment.

    Contract — section order, because it is the reading order:

    1. `COMMENT_MARKER`, then a one-line verdict: overall severity, column
       count, downstream entity count.
    2. Severity table: one row per changed column, with the score and the two
       or three factors that dominated it. A reader must be able to see why.
    3. Affected entities: grouped by column, deep-linked into DataHub, with hop
       distance and the transformation that carries the change.
    4. Owners to notify, @-mentioned once each, with the entity they own.
    5. Untrusted findings, when any: what the text said, where it was, and the
       explicit statement that it did not affect the score.
    6. Generated fixes: compiled ones first with a link to the fix branch,
       failed ones collapsed with their compiler output.
    7. Degradations.
    8. A footer naming the severity rule version and linking the schema.

    Must be idempotent: same report in, byte-identical markdown out.
    """
    raise StubNotImplementedError(
        f"{_T}.render_comment",
        OWNER_B,
        "ImpactReport -> full markdown PR comment, idempotent, untrusted text quoted",
    )


def render_severity_table(report: ImpactReport) -> str:
    """Render the per-column severity table with its factor breakdown.

    Contract: show `score`, `level`, and the highest-contributing factors with
    their raw values, so a reviewer can re-derive the number by hand. Include
    the rule version.
    """
    raise StubNotImplementedError(
        f"{_T}.render_severity_table", OWNER_B, "per-column severity table with factor breakdown"
    )


def quote_untrusted(text: str) -> str:
    """Quote untrusted text so it cannot alter the comment or mention anyone.

    Contract: fence it, neutralise `@` mentions and HTML, and preserve the
    content so a human can read exactly what was written. Escaping is for
    rendering safety only — the report's copy stays verbatim.
    """
    raise StubNotImplementedError(
        f"{_T}.quote_untrusted",
        OWNER_B,
        "render-safe quoting of untrusted text: no mentions, no HTML, content preserved",
    )
