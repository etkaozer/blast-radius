"""Exception types for `core/`.

The shared ones live in `contracts.errors` so that OWNER B can use them without
reading `core/` — see that module for why. They are re-exported here so engine
code has one place to import from.
"""

from __future__ import annotations

from contracts.errors import OWNER_A, OWNER_B, BlastRadiusError, StubNotImplementedError

__all__ = [
    "OWNER_A",
    "OWNER_B",
    "AgentError",
    "BlastRadiusError",
    "ConfigurationError",
    "DataHubAccessError",
    "FixValidationError",
    "StubNotImplementedError",
    "WriteCapabilityError",
]


class AgentError(BlastRadiusError):
    """A call to the language model failed.

    Never fatal to an analysis. Severity, lineage and the untrusted findings are
    all computed before the model is reached, so the caller downgrades to a
    report with no prose and an `llm_explanation` degradation rather than
    losing a review that was already complete.
    """


class ConfigurationError(BlastRadiusError):
    """Required configuration is missing or contradictory."""


class DataHubAccessError(BlastRadiusError):
    """A read against DataHub failed, or returned something the contract forbids."""


class WriteCapabilityError(BlastRadiusError):
    """The DataHub write path is unavailable and no fallback applies.

    Raised rather than silently skipping the write-back: a review that believes
    it recorded a finding, and did not, is worse than one that failed loudly.
    """


class FixValidationError(BlastRadiusError):
    """A generated fix could not be validated, and the retry budget is exhausted."""
