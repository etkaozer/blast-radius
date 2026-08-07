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
| `core/datahub/mcp_session.py` | The async MCP stdio transport, confined so nothing above it is async |
| `core/datahub/hybrid.py` | The composed reader; see "Two access paths, and why there are three" |
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
   observe between `McpDataHubReader` and `SdkDataHubReader` is a bug — *except
   where one of them provably cannot answer a read at all*, which is the case
   documented below. That exception is narrow and each instance is named in
   code; it is not a licence to let the two drift.
5. **The clock is a parameter, not a call.** Pure functions take `detected_at`
   and `generated_at`; only `core/pipeline.py` and `core/cli.py` read the
   actual time.

## Two access paths, and why there are three readers

`mcp-server-datahub` 0.6.0 cannot serve two of the nine reads in
`DataHubReader` on an open-source DataHub. This was established by reading the
installed package — its registered tool list and the GraphQL documents in
`gql/` — not by inference:

- **data contracts**: no tool exists, and no `.gql` in the package mentions
  contracts;
- **assertions**: `get_dataset_assertions` is declared
  `@min_version(cloud="0.3.16")` with no OSS minimum, which that decorator's own
  docstring defines as "not available on OSS", and it is additionally hidden
  unless the server is started with `DATA_QUALITY_TOOLS_ENABLED=true`.

Those two reads feed `contract_presence` (12 points) and `assertion_presence`
(4). So a bare MCP reader on an open-source catalog cannot produce two of the
seven severity factors.

`McpDataHubReader` therefore **raises `DataHubCapabilityError`** for both rather
than returning an empty tuple. An empty tuple is a scored claim: it would zero
the factor and read as "this dataset has no contract", which is a different
statement from "this path cannot see contracts".

`HybridDataHubReader` resolves it by serving those two — plus
`get_dataset_queries`, because MCP's query tool reads catalogued Query entities
rather than the `datasetUsageStatistics` aspect the factor is defined on — from
the SDK, and reporting `access_path` as `"mcp+sdk"`. `build_reader` returns it
for `BLAST_RADIUS_DATAHUB_MODE=mcp`. Lineage stays on MCP.

That third `access_path` value is why `impact_report.schema.json` is at
`schema_version` 1.1.0. **It is a `contracts/` change and needs OWNER B's
approval**, because a renderer matching exhaustively on two values breaks on a
third.

## Where to start

```bash
uv run blast-radius stubs --owner A
```
