"""blast-radius CI half. OWNER B (teammate).

| package | role |
| --- | --- |
| `ci.diff` | sqlglot-based deterministic extraction of changed columns |
| `ci.render` | ImpactReport -> markdown pull-request comment |
| `ci.publish` | post the comment, push the fix branch |

Imports allowed: `contracts` (the frozen interface) and `core.errors` for the
shared exception types. Nothing else from `core/`.
"""
