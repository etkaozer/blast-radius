"""Heuristic detection of text addressed at the review agent.

## What this is

A reporter. It looks at untrusted free text and flags passages that read like
instructions aimed at an automated reviewer, so that the PR comment can say
"the description for this column tells review agents to mark it low severity"
next to a lineage graph showing three consumers. That juxtaposition is the most
useful thing blast-radius can put in front of a human.

## What this is not

A defence. Detecting adversarial instructions in natural language by pattern
matching is not a solvable problem: the patterns below catch the obvious cases
and will miss a paraphrase, another language, a base64 blob, or an instruction
split across two fields. Every finding it produces carries `is_heuristic: true`
and `effect_on_severity: "none"` as schema constants, and both are honest.

The actual defence is architectural and lives elsewhere: severity is computed
from downstream count, hop distance, observed query usage and contract presence
by `core/severity/`, which runs before this module and has no parameter through
which prose could arrive. If this detector returned nothing at all, a severity
score would still be correct. See `docs/THREAT_MODEL.md`.

## Why a structured finding rather than a boolean

A boolean forces a decision the tool is not entitled to make. `is_this_evil()`
returning True invites a caller to drop the text, block the PR, or re-score;
all three are wrong. A finding says what matched, where, how confident the
heuristic is, and what it did about it (nothing), and leaves the judgement to
the reviewer.
"""

from __future__ import annotations

from typing import Final

from contracts.models import UntrustedFinding
from core.untrusted.envelope import UntrustedEnvelope

DETECTOR_VERSION: Final[str] = "det-v1"

#: The stable vocabulary of pattern ids. These appear in golden fixtures and in
#: rendered PR comments, so adding one is fine and renaming one is an interface
#: change. Keep in sync with contracts/fixtures/03_adversarial_description/.
PATTERN_AGENT_ADDRESSED_IMPERATIVE: Final[str] = "inj-agent_addressed_imperative"
PATTERN_SEVERITY_DIRECTIVE: Final[str] = "inj-severity_directive"
PATTERN_ANALYSIS_SUPPRESSION: Final[str] = "inj-analysis_suppression"
PATTERN_AUTHORITY_CLAIM: Final[str] = "inj-authority_claim"
PATTERN_ROLE_OVERRIDE: Final[str] = "inj-role_override"

KNOWN_PATTERN_IDS: Final[frozenset[str]] = frozenset(
    {
        PATTERN_AGENT_ADDRESSED_IMPERATIVE,
        PATTERN_SEVERITY_DIRECTIVE,
        PATTERN_ANALYSIS_SUPPRESSION,
        PATTERN_AUTHORITY_CLAIM,
        PATTERN_ROLE_OVERRIDE,
    }
)

#: Longest excerpt copied into a finding. Enough context for a human to judge
#: the match, short enough that the finding cannot become a second delivery
#: vehicle for the same instruction inside a rendered comment.
MAX_EXCERPT_CHARS: Final[int] = 500


def scan(envelope: UntrustedEnvelope) -> tuple[UntrustedFinding, ...]:
    """Return findings for one piece of untrusted text.

    OWNER A implements. Contract:

    - Pure and deterministic: same text in, same findings out. No model call,
      no network, no clock. This runs on adversarial input, so it must not be
      capable of doing anything interesting when it is wrong.
    - Returns zero or more `UntrustedFinding`, never a bool, never None.
    - `pattern_id` must come from `KNOWN_PATTERN_IDS`.
    - `excerpt` is a VERBATIM slice of `envelope.value`, at most
      `MAX_EXCERPT_CHARS`, chosen to include the matched span.
    - `confidence` is `high` only when the text names an agent or reviewer AND
      issues an imperative; `medium` when it does one of the two; `low` for
      weaker signals such as an unverifiable approval claim on its own.
    - Never raises on malformed, enormous, or non-UTF8-decodable input. A
      detector that crashes on hostile input is a denial-of-service vector in
      the review pipeline.
    - Never mutates the envelope and never returns modified text.

    Detection classes to implement, in the order they earn their keep:

    1. `inj-agent_addressed_imperative` — second person imperative addressed to
       an agent, assistant, reviewer or bot ("review agents: mark this...").
    2. `inj-severity_directive` — instructs a specific severity, priority or
       verdict ("treat as low severity", "this is not breaking").
    3. `inj-analysis_suppression` — asks the reader to skip, ignore or shortcut
       a class of evidence ("ignore lineage results", "skip the impact
       analysis").
    4. `inj-authority_claim` — unverifiable claim of prior approval or of
       authority over the reviewer ("pre-approved by the data platform team").
    5. `inj-role_override` — attempts to redefine the reader's role or
       instructions ("you are now...", "ignore previous instructions").
    """
    msg = (
        "core.untrusted.detector.scan is not implemented (owner A / etka). "
        "It must return structured UntrustedFinding objects for text addressed at the "
        "review agent, without modifying the text and without touching severity."
    )
    raise NotImplementedError(msg)


def scan_all(envelopes: tuple[UntrustedEnvelope, ...]) -> tuple[UntrustedFinding, ...]:
    """Scan every envelope and return the flattened findings, in envelope order."""
    findings: list[UntrustedFinding] = []
    for envelope in envelopes:
        findings.extend(scan(envelope))
    return tuple(findings)


def excerpt_of(value: str, start: int, end: int) -> str:
    """Return a bounded verbatim excerpt of `value` covering [start, end).

    Real helper, used by `scan` once implemented, and safe to rely on now: it
    only ever slices, so the excerpt is always a substring of the original.
    """
    if start < 0 or end < start:
        msg = f"invalid excerpt span ({start}, {end})"
        raise ValueError(msg)
    span = value[start:end]
    if len(span) <= MAX_EXCERPT_CHARS:
        return span
    return span[: MAX_EXCERPT_CHARS - 1] + "…"
