"""Untrusted free-text handling: envelopes for prompts, heuristics for reporting.

Nothing in this package may influence a severity score. `core.severity` does not
import it, and it does not import `core.severity`.
"""

from core.untrusted.detector import (
    DETECTOR_VERSION,
    KNOWN_PATTERN_IDS,
    MAX_EXCERPT_CHARS,
    scan,
    scan_all,
)
from core.untrusted.envelope import (
    ENVELOPE_VERSION,
    PREAMBLE,
    UntrustedEnvelope,
    envelope_ids,
    render_block,
    wrap_all,
)

__all__ = [
    "DETECTOR_VERSION",
    "ENVELOPE_VERSION",
    "KNOWN_PATTERN_IDS",
    "MAX_EXCERPT_CHARS",
    "PREAMBLE",
    "UntrustedEnvelope",
    "envelope_ids",
    "render_block",
    "scan",
    "scan_all",
    "wrap_all",
]
