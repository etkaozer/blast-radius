# examples/

**Real generated artifacts.** Not fixtures, not illustrations — actual output
from actual runs, committed so that a reader can see what the tool produces
without installing anything.

The distinction matters and is worth keeping sharp:

| | `contracts/fixtures/` | `examples/` |
| --- | --- | --- |
| What | Hand-authored golden data | Real tool output |
| Purpose | The frozen interface both owners code against | Evidence, for a reader |
| Changes | Only by a PR with both approvals | Append-only |
| Tested | Yes, exhaustively | No |

Nothing in here is a fixture. If you find yourself wanting to test against a
file in `examples/`, the file belongs in `contracts/fixtures/` instead — and
that is an interface change.

## Layout

| Path | Owner | What goes in it |
| --- | --- | --- |
| `examples/reports/` | A (@etka) | `impact_report.json` from real runs, plus the `writeback_record.json` written to DataHub |
| `examples/fixes/` | A (@etka) | Generated downstream fixes, compiled and not, with their compiler output |

`examples/` is **append-only**. Never rewrite or delete an artifact someone else
committed — an example whose contents changed silently is worse than no example,
because a reader has no way to know.

## Naming

```
examples/reports/<date>-<repo>-pr<number>-<slug>.json
examples/fixes/<date>-<repo>-pr<number>/<target-path>
```

For instance:

```
examples/reports/2026-03-18-acme-analytics-pr214-ltv-removal.json
examples/fixes/2026-03-18-acme-analytics-pr214/models/marts/customer_ltv.sql
```

The date is when the run happened, so a reader can tell a report generated
against an early severity rule version from a current one. Every report carries
`tool.severity_rule_version` internally as well.

## Before committing an artifact

1. **Check it for secrets.** Reports contain URNs, owner handles and query
   counts from whatever catalog produced them. A real deployment's URNs may
   name internal systems.
2. **Keep it whole.** Commit the report as produced. A trimmed report is an
   illustration, and illustrations belong in the README.
3. **Include the failures.** A fix that did not compile is more informative than
   three that did — it shows the compiler gate is real. Do not curate those out.

## Status

Empty for now. It fills up as soon as the pipeline runs end to end against a
live DataHub; `docs/JUDGING.md` tracks that as an open TODO.
