"""The LLM layer: prose explanations and candidate fix code. Nothing else.

Import boundary: `core.severity` and `core.impact` must never import this
package. See `core/tests/test_module_boundaries.py`.
"""

from core.agent.client import MAX_EXPLANATION_CHARS, AnthropicAgent, FixCandidate
from core.agent.prompts import (
    EXPLANATION_PROMPT_VERSION,
    EXPLANATION_SYSTEM,
    FIX_PROMPT_VERSION,
    FIX_SYSTEM,
    RETRY_SUFFIX,
)

__all__ = [
    "EXPLANATION_PROMPT_VERSION",
    "EXPLANATION_SYSTEM",
    "FIX_PROMPT_VERSION",
    "FIX_SYSTEM",
    "MAX_EXPLANATION_CHARS",
    "RETRY_SUFFIX",
    "AnthropicAgent",
    "FixCandidate",
]
