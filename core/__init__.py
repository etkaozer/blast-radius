"""blast-radius engine. OWNER A (etka).

Layout:

| package | role | deterministic? |
| --- | --- | --- |
| `core.datahub` | DataHub reads over MCP or the Python SDK | yes |
| `core.impact` | downstream traversal and fact collection | yes |
| `core.severity` | scoring; the model cannot reach it | yes |
| `core.untrusted` | envelopes for prompts, heuristics for reporting | yes |
| `core.agent` | prose explanations and candidate fix code | no |
| `core.validate` | dbt compile gate and bounded retry | yes |
| `core.writeback` | DataHub mutations and `doctor` | yes |

`core.pipeline` sequences them, and the order is a control: severity is computed
before untrusted text is read and before any model call.
"""

from core.version import VERSION

__all__ = ["VERSION"]
