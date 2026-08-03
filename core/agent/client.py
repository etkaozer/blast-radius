"""Anthropic API calls. The only place a model is used, and only for two things.

The model writes prose explanations and candidate fix code. It does not set
severity, does not decide what is breaking, and does not gate any write. That is
not a policy this module enforces at runtime — it is a consequence of what this
module is allowed to return: an `Explanation`, whose `is_model_generated` and
`disclaimer` fields are schema constants, and a string of candidate code, which
is worthless until `core.validate` compiles it.

`core.severity` cannot import this module. `core/tests/test_module_boundaries.py`
asserts it, in both directions: by inspecting the import graph, and by importing
`core.severity` in a clean subprocess and asserting that neither `core.agent` nor
`anthropic` ended up in `sys.modules`.
"""

from __future__ import annotations

from dataclasses import dataclass

from contracts.models import ColumnImpact, Explanation
from core.agent.prompts import EXPLANATION_PROMPT_VERSION, FIX_PROMPT_VERSION
from core.errors import OWNER_A, StubNotImplementedError
from core.untrusted.envelope import UntrustedEnvelope

_T = "core.agent.client.AnthropicAgent"

#: Hard cap on explanation length, matching `explanation.text` in the schema.
MAX_EXPLANATION_CHARS = 4000


@dataclass(frozen=True, slots=True)
class FixCandidate:
    """One candidate file produced by the model, before validation.

    Deliberately not a `GeneratedFix`: that type carries a `validation` block,
    and there is no way to construct one here. Model output becomes a
    `GeneratedFix` only after `core.validate` has compiled it.
    """

    target_repo_path: str
    content: str
    language: str
    attempts: int


class AnthropicAgent:
    """Prose and candidate code, via the Anthropic API."""

    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    @property
    def model(self) -> str:
        """The model id recorded in the report."""
        return self._model

    def explain(
        self,
        impact: ColumnImpact,
        untrusted: tuple[UntrustedEnvelope, ...],
    ) -> Explanation:
        """Write the human-readable explanation for one column impact.

        Contract:

        - System prompt is `prompts.EXPLANATION_SYSTEM`, unmodified.
        - The facts in the user message are rendered from `impact` — entities,
          hop distances, transformations, owners, assertions, contracts, query
          counts. The severity SCORE may be included as a fact but must never
          be presented as a question.
        - Untrusted text goes into the prompt only through
          `core.untrusted.envelope.render_block(untrusted)`, never concatenated
          raw, and never omitted: an explanation that cannot see the misleading
          description cannot report on it.
        - Returns an `Explanation` with `model=self._model`,
          `prompt_version=EXPLANATION_PROMPT_VERSION` and
          `untrusted_inputs_referenced` set to the ids that were placed in the
          prompt.
        - Truncate to `MAX_EXPLANATION_CHARS`; the schema enforces it anyway and
          a rejected report at the end of a run is a bad way to find out.
        - On API failure, raise. The caller downgrades to a report with
          `explanation=None` plus an `llm_explanation` degradation, because a
          review without prose is still a useful review.
        """
        raise StubNotImplementedError(
            f"{_T}.explain",
            OWNER_A,
            f"Anthropic call with EXPLANATION_SYSTEM ({EXPLANATION_PROMPT_VERSION}); "
            "untrusted text only via render_block; returns a marked Explanation",
        )

    def propose_fix(
        self,
        impact: ColumnImpact,
        target_repo_path: str,
        current_content: str,
        compiler_output: str | None = None,
    ) -> FixCandidate:
        """Write a candidate replacement for one downstream file.

        Contract:

        - System prompt is `prompts.FIX_SYSTEM`, unmodified. On a retry, append
          `prompts.RETRY_SUFFIX` formatted with `compiler_output`.
        - Returns the complete file contents, with no fences and no commentary.
          Strip a leading ```sql fence defensively; models add them.
        - `attempts` counts from 1 and is carried into the report so a reader
          can see which fixes were hard.
        - This function never writes to disk and never decides whether the fix
          is good. `core.validate` does both.
        """
        raise StubNotImplementedError(
            f"{_T}.propose_fix",
            OWNER_A,
            f"Anthropic call with FIX_SYSTEM ({FIX_PROMPT_VERSION}); returns complete "
            "file contents as a FixCandidate, unvalidated",
        )
