# core/ — OWNER A (@etka)

**Before editing any file, check the ownership table in the root
[CLAUDE.md](../CLAUDE.md). If the file is outside your scope, stop and tell the
user instead of editing.**

## Scope

The engine. Everything between "a parsed diff arrived" and "a report and a
DataHub write-back went out".

| Package | Responsibility |
| --- | --- |
| `core/datahub/` | Reads over MCP (`mcp-server-datahub`) and the Python SDK (`acryl-datahub`), behind one protocol |
| `core/impact/` | Column-level lineage traversal; collection of owners, assertions, contracts, usage |
| `core/severity/` | Deterministic scoring. Pure functions, fully unit tested |
| `core/untrusted/` | Envelope for free text entering prompts; heuristic detection for reporting |
| `core/agent/` | Anthropic calls: prose explanations and candidate fix code only |
| `core/validate/` | `dbt compile` gate for generated fixes, with a bounded retry loop |
| `core/writeback/` | DataHub mutations, capability detection, `blast-radius doctor` |
| `core/pipeline.py` | Stage sequencing. The order is a security control |
| `core/cli.py` | `blast-radius analyze | doctor | stubs` |

## What this directory may import

- `contracts` — the frozen interface. Always.
- The standard library.
- `pydantic`, `jsonschema`, `click` — declared dependencies.
- `anthropic` — **only** inside `core/agent/`.
- `acryl-datahub` — **only** inside `core/datahub/` and `core/writeback/`.

## What this directory must NOT read

Do not open, grep or reason about:

- `ci/` — OWNER B's diff extraction, rendering and publishing
- `env/` — OWNER B's DataHub quickstart, dbt project and seeding
- `.github/workflows/` — OWNER B's Action

If you need to know what OWNER B produces or consumes, read
`contracts/change_set.schema.json`, `contracts/impact_report.schema.json` and
the golden fixtures in `contracts/fixtures/`. That is what they are for. If the
answer is not there, the contract has a gap — say so, and do not go looking in
`ci/`.

## Internal import boundary (enforced by a test)

```
core.severity  ─X→  core.agent          # must never happen
core.impact    ─X→  core.agent          # must never happen
core.agent     ─X→  core.severity       # must never happen, symmetric
```

`core/tests/test_module_boundaries.py` checks this statically (by parsing
imports) and dynamically (by importing `core.severity` in a clean subprocess and
asserting `core.agent` and `anthropic` are absent from `sys.modules`).

The reason: severity must be computable from graph facts alone, by code the
model cannot reach. If the agent could import `compute`, a later refactor could
have the model "suggest" a score, and the resulting object would still carry
`computed_by: "deterministic"` while being nothing of the sort.

## Rules specific to this directory

1. **Stubs raise, they never fake.** `raise StubNotImplementedError(target,
   OWNER_A, contract)` with a docstring describing the intended behaviour. No
   plausible placeholder data, ever.
2. **Wrap untrusted text at the boundary.** Any free text that comes back from
   DataHub — field descriptions, documentation, glossary terms — is wrapped
   with `core.untrusted.envelope` inside `core/datahub/`, before it escapes as
   a bare `str`. Once it is a plain string, nothing downstream can tell it apart
   from a trusted one.
3. **Degrade visibly.** A capability that was unavailable becomes a
   `Degradation` in the report. Never let "we could not measure it" look like
   "there is nothing there".
4. **Both access paths must behave identically.** Any difference a caller can
   observe between `McpDataHubReader` and `SdkDataHubReader` is a bug.
5. **The clock is a parameter, not a call.** Pure functions take `detected_at`
   and `generated_at`; only `core/pipeline.py` and `core/cli.py` read the
   actual time.

## Where to start

```bash
uv run blast-radius stubs --owner A
```
