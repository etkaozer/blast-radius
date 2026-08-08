# Live verification against a real DataHub

**Audience: OWNER B (@teammate), on the machine with Docker.** Written by OWNER A
(@etka), who has no Docker and has therefore never executed a single DataHub call
in this repository.

Everything in `core/` that touches DataHub is written against the installed
server's own type definitions and GraphQL documents. That is good evidence about
*shapes* and no evidence at all that it *works*. This checklist is how that
becomes a real answer.

## How to read this

Each step is marked:

- **[SOLO]** — run it on your own, before the joint session. If it passes, move
  on. If it fails, stop and send OWNER A the output; do not debug alone.
- **[BOTH]** — for the shared screen session. Needs OWNER A to read the failure
  against the code.

Steps are ordered so that **the riskiest unverified assumptions fail first**.
Step 4 is the one most likely to be wrong. If you only get through step 5 before
the session, that is the right five steps to have done.

### A note on the reading rule

`CLAUDE.md` says OWNER B does not read `core/`. **That rule is suspended for the
files named in this document, for this session only.** You are being asked to
run code you did not write and report where it broke; the file:line pointers are
there so you can paste a location, not so you can fix it. Fixes to `core/` are
still OWNER A's.

---

## Step 0 — Prerequisites [SOLO]

Everything below assumes all of this is already true. Most of it you have.

| Requirement | Check |
| --- | --- |
| DataHub quickstart running | `datahub docker check` |
| Demo project ingested and seeded | your `env/seed_demo.py` has been run |
| Python 3.11+ and `uv` | `uv --version` |
| Repo on the merged `main` | `git log --oneline -1` |

Install **all** extras. The MCP path needs the new `mcp` extra, which did not
exist before this branch:

```bash
uv sync --all-extras --group dev
```

Install the MCP server itself. It is deliberately **not** a dependency of this
project — it is a separate process an operator installs, exactly as a DataHub
agent would:

```bash
uv tool install mcp-server-datahub
mcp-server-datahub --version      # expect 0.6.0 or newer
```

Set the environment. On an open-source quickstart there is **no token**, and
that is now fine — do not invent one:

```bash
# PowerShell
$env:DATAHUB_GMS_URL = "http://localhost:8080"
```

```bash
# bash/zsh
export DATAHUB_GMS_URL=http://localhost:8080
```

These four URNs are used throughout. They are yours, from the seeded catalog:

```
DATASET    urn:li:dataset:(urn:li:dataPlatform:dbt,blast_radius_demo.main.dim_customers,PROD)
COLUMN     customer_lifetime_value
HOP 1      urn:li:dataset:(urn:li:dataPlatform:dbt,blast_radius_demo.main.customer_ltv,PROD)
HOP 2      urn:li:dataset:(urn:li:dataPlatform:dbt,blast_radius_demo.main.mart_exec_summary,PROD)
```

---

## Step 1 — The repo itself is sane [SOLO]

**Command**

```bash
make check
uv run blast-radius stubs
```

**Correct output**

`make check` ends with `507 passed` (or more), no ruff or mypy output. `stubs`
prints `0 stub(s) remaining` if your `env/seed_demo.py` work is merged, or lists
only `env/seed_demo.py` entries if it is not.

**Likely failure**

Any `owner A (etka)` entry in the stub list means the merge lost something —
this branch took OWNER A's count to zero. Send the list.

If `make check` fails on Windows in `core/tests/test_validate_compile.py` or
`test_write_capabilities.py`, that is the known POSIX `#!/bin/sh` assumption you
already diagnosed. It is **not** a blocker for this session; note it and
continue.

---

## Step 2 — The mcp+sdk composition actually resolves [SOLO]

This costs nothing and needs no DataHub, so it goes before anything expensive.
It verifies that asking for the MCP path really does build **two** readers, not
one — the whole reason the report can say `mcp+sdk`.

**Command**

```bash
uv run python -c "
from core.config import Settings
from core.datahub.factory import build_reader
s = Settings.from_env().with_overrides(datahub_mode='mcp')
r = build_reader(s)
print('access_path :', r.access_path)
print('reader      :', type(r).__name__)
print('mcp half    :', type(r._mcp).__name__)
print('sdk half    :', type(r._sdk).__name__)
"
```

**Correct output — exactly this**

```
access_path : mcp+sdk
reader      : HybridDataHubReader
mcp half    : McpDataHubReader
sdk half    : SdkDataHubReader
```

**Likely failure**

- `access_path : mcp` and `reader : McpDataHubReader` — `build_reader` did not
  compose. Look at `core/datahub/factory.py:32`, the `if settings.datahub_mode
  == "mcp"` branch.
- `AttributeError: 'HybridDataHubReader' object has no attribute '_mcp'` — the
  attribute was renamed; `core/datahub/hybrid.py:85`.
- `ModuleNotFoundError: mcp` — `uv sync --all-extras` did not take. The import is
  lazy on purpose, so this should surface as a clean message from
  `core/datahub/mcp_session.py:155`, not a traceback.

**Second half of the same step** — the two reads MCP cannot serve must *raise*,
not quietly return empty. An empty tuple would zero a 12-point and a 4-point
severity factor and read as a measurement:

```bash
uv run python -c "
from core.datahub.mcp_client import McpDataHubReader
from core.errors import DataHubCapabilityError
r = McpDataHubReader('mcp-server-datahub','http://localhost:8080')
for name in ('get_assertions','get_data_contracts'):
    try:
        getattr(r, name)('urn:li:dataset:(urn:li:dataPlatform:dbt,x,PROD)')
        print(name, 'DID NOT RAISE  <- bug')
    except DataHubCapabilityError as e:
        print(name, '-> DataHubCapabilityError:', str(e)[:60])
"
```

Both lines must say `-> DataHubCapabilityError`. Neither should start a
subprocess or take more than a moment.

---

## Step 3 — The MCP server starts, and the tool names are real [SOLO]

Every MCP call in `core/` is driven by tool names read out of
`mcp-server-datahub` 0.6.0 on OWNER A's machine. If your installed version
registers different names, **half this project does not work** and we need to
know now, not at step 7.

**Command**

```bash
uv run python -c "
import asyncio, os
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REQUIRED_READS = {'search','get_entities','list_schema_fields',
                  'get_lineage','get_lineage_paths_between','get_dataset_queries'}
REQUIRED_WRITES = {'add_tags','add_structured_properties','save_document','add_owners'}

async def main():
    p = StdioServerParameters(
        command='mcp-server-datahub', args=[],
        env={'DATAHUB_GMS_URL': os.environ.get('DATAHUB_GMS_URL','http://localhost:8080'),
             'TOOLS_IS_MUTATION_ENABLED': 'true'})
    async with stdio_client(p) as (r,w):
        async with ClientSession(r,w) as s:
            await s.initialize()
            names = {t.name for t in (await s.list_tools()).tools}
            print('tools:', len(names))
            print('missing reads :', sorted(REQUIRED_READS - names) or 'none')
            print('missing writes:', sorted(REQUIRED_WRITES - names) or 'none')
asyncio.run(main())
"
```

**Correct output**

```
tools: 20
missing reads : none
missing writes: none
```

**Likely failure**

- **Anything listed as missing.** This is the highest-value failure in the whole
  document. Send the full sorted tool list. The names are consumed in
  `core/datahub/mcp_client.py` (search at `:239`, get_entities `:260`,
  list_schema_fields `:272`, get_lineage `:331`, get_lineage_paths_between
  `:380`, get_dataset_queries `:409`) and in `core/writeback/writer.py`
  (add_tags `:177`, add_structured_properties `:191`, save_document `:207`,
  add_owners `:220`).
- **`missing writes` non-empty but reads fine** — mutation tools are hidden
  unless the server was started with `TOOLS_IS_MUTATION_ENABLED=true`. The
  script above sets it; `core/writeback/writer.py:157` sets it for the real
  writer too. If they are still missing, your server is older than v0.5.0.
- **The command hangs.** The server is waiting on something. Ctrl-C and run
  `mcp-server-datahub --version` alone.

---

## Step 4 — The raw lineage payload [SOLO] — *the riskiest step*

**Read this even if everything above passed.**

`McpDataHubReader.get_lineage` turns the server's `searchAcrossLineage` response
into `DownstreamEntity` objects with a full `path`. The reconstruction in
`core/datahub/mcp_client._hops_from_path_nodes` was written by reading the
GraphQL document `gql/entity_details.gql` inside the installed package —
specifically the `searchResults { entity, paths { path { urn, type,
... on SchemaFieldEntity { fieldPath, parent } } }, degree }` shape. **Nobody has
ever seen a real response.**

This step captures one. Do it before the session and send OWNER A the file —
it is the single most useful artifact you can bring.

**Command**

```bash
uv run python -c "
import json, os
from core.datahub.mcp_session import McpSession
URN='urn:li:dataset:(urn:li:dataPlatform:dbt,blast_radius_demo.main.dim_customers,PROD)'
s = McpSession('mcp-server-datahub', os.environ.get('DATAHUB_GMS_URL','http://localhost:8080'))
try:
    payload = s.call('get_lineage', {'urn': URN, 'column': 'customer_lifetime_value',
                                     'upstream': False, 'max_hops': 3, 'max_results': 200})
    open('lineage_raw.json','w',encoding='utf-8').write(json.dumps(payload, indent=2))
    print('wrote lineage_raw.json')
    print('top-level keys:', list(payload) if isinstance(payload, dict) else type(payload).__name__)
    results = (payload or {}).get('searchResults') or []
    print('searchResults:', len(results))
    if results:
        print('first result keys:', list(results[0]))
        print('has paths:', bool(results[0].get('paths')))
        print(json.dumps(results[0].get('paths'), indent=2)[:1500])
finally:
    s.close()
"
```

**Correct output**

`searchResults` is **3 or more**, `first result keys` includes `entity`,
`paths` and `degree`, and `has paths: True`. The printed `paths` contain nodes
with `fieldPath` and `parent`.

**Likely failures, in the order they matter**

1. **`searchResults: 0`.** The column-level walk found nothing. Re-run with the
   `'column'` key deleted from the arguments. If that returns results, the
   fine-grained edges are not where we think — the `column` argument is passed
   at `core/datahub/mcp_client.py:329`.
2. **`has paths: False`.** Fatal for the mapping.
   `_hops_from_path_nodes` (`core/datahub/mcp_client.py:520`) returns `()` with
   no path, and `get_lineage` then *drops the entity entirely*
   (`core/datahub/mcp_client.py:344`) — deliberately, because an entity without a
   path is not auditable. The visible symptom later would be **zero downstream
   entities and therefore a near-zero severity score**, which is exactly the
   silent-wrong-answer this project exists to avoid. Send `lineage_raw.json`.
3. **Path nodes have no `fieldPath`/`parent`.** The mapping falls back to
   dataset-level steps and `from_column`/`to_column` come out `None`. Severity
   survives; the reviewer loses the column-to-column detail. Recoverable, but we
   want to know.
4. **`top-level keys` is not what we expect** — e.g. the payload is a list, or
   nests under `data`. `McpSession.decode_result`
   (`core/datahub/mcp_session.py:238`) unwraps FastMCP's `{"result": ...}`
   envelope; if the server wraps differently, that is where to look.

---

## Step 5 — The mapping produces a usable path [SOLO]

Same call, now through the reader, so we see contract objects instead of JSON.

**Command**

```bash
uv run python -c "
import os
from core.datahub.mcp_client import McpDataHubReader
URN='urn:li:dataset:(urn:li:dataPlatform:dbt,blast_radius_demo.main.dim_customers,PROD)'
r = McpDataHubReader('mcp-server-datahub', os.environ.get('DATAHUB_GMS_URL','http://localhost:8080'))
try:
    for e in r.get_lineage(URN, column='customer_lifetime_value'):
        print(f'{e.hop_distance}  {e.entity_type:10} {e.name}')
        print('     via_column:', e.via_column)
        print('     path      :', [(h.from_column, h.to_column) for h in e.path])
finally:
    r.close()
"
```

**Correct output**

At least `customer_ltv` at hop 1 and `mart_exec_summary` at hop 2, with
`via_column` set (`ltv_usd`, `total_ltv`) and path tuples showing the
column-to-column mapping — the chain you verified in GMS:

```
dim_customers.customer_lifetime_value -> customer_ltv.ltv_usd -> mart_exec_summary.total_ltv
```

**Likely failure**

- **Empty output but step 4 showed results** — the mapping is dropping
  everything. `core/datahub/mcp_client.py:344` is the `if not hops: continue`.
- **`via_column: None`** — failure mode 3 from step 4.
- **Hop distances all 1** — `degree` is missing from the response and the code
  fell back to path length (`core/datahub/mcp_client.py:347`). This *inflates*
  `hop_proximity`, worth up to 15 points, so it is a real scoring bug and not
  cosmetic.
- **`pydantic.ValidationError` on `name` or `entity_type`** — an entity type the
  contract does not publish, or an empty name. `_entity_ref`
  (`core/datahub/mcp_client.py:138`) and `entity_type_of` in
  `core/datahub/mapping.py:87`.

---

## Step 6 — The write round-trip on a token-less quickstart [SOLO]

Until this branch, `capabilities.detect` required `DATAHUB_GMS_TOKEN` before it
would call the SDK write path usable. Your quickstart has metadata service
authentication **disabled** and issues no token, so blast-radius reported "no
write path available" on exactly the deployment the demo runs on. That is fixed
(`core/writeback/capabilities.py:233`); this step is what proves it.

`doctor` writes the impact-record structured property to a dedicated scratch
URN, reads it back with a *different* client than the one that wrote it,
compares, and hard-deletes it in a `finally`. It never touches a real dataset.

**Command**

```bash
uv run blast-radius doctor
```

**Correct output**

```
  ✓ write path           Falling back to the Python SDK write path: ...
  ✓ write round trip     wrote and read back impactRecord over the sdk path
```

Exit code 0. Note the token line stays a `!` warning — that is intended, not a
failure.

**Likely failure**

- **`✗ write path  No write path available`** — the token fix did not land, or
  GMS is unreachable. `core/writeback/capabilities.py:233` is the
  `sdk_available=` expression; it must not mention `token`.
- **`✗ write round trip ... wrote the property but read nothing back`** — the
  emit succeeded and the read found nothing. Usually the structured property
  *definition* does not exist in DataHub. This is the most likely real failure
  here, and it is the one to bring to the session: OSS DataHub may require
  `urn:li:structuredProperty:io.blastradius.impactRecord` to be defined before
  values can be assigned. Written at `core/writeback/writer.py:352`.
- **`... does not match what was written`** — DataHub coerced the value.
  `record_property_value` (`core/writeback/writer.py:103`) emits compact sorted
  JSON as a single string.
- **A leftover `blast_radius.doctor.scratch` dataset in the UI** — cleanup
  failed. `_delete_scratch`, `core/writeback/doctor.py:275`. Harmless; delete it
  by hand and tell us.

---

## Step 7 — A change set from the real catalog [BOTH]

Everything so far read the catalog. This is the first step that runs the whole
pipeline, and it needs an input.

The fixtures in `contracts/fixtures/` carry **synthetic snowflake URNs** and your
catalog is on the `dbt` platform, so a fixture replayed against the live catalog
resolves nothing. We need a change set generated from the real thing.

**Generating it is yours** — `ci/diff` is OWNER B's half and OWNER A has not
read it and will not. Produce a `change_set.json` describing the removal of
`customer_lifetime_value` from `dim_customers`, with real `dbt` platform URNs.

```bash
# your command here — whatever ci/diff exposes for two SHAs
uv run blast-radius-ci <...> --out live_change_set.json
```

**Then, before running the pipeline, validate it against the frozen schema.**
This is the boundary between our halves and the cheapest place to catch a
mismatch:

```bash
uv run python -c "
from pathlib import Path
from contracts.loader import load_change_set
cs = load_change_set(Path('live_change_set.json'))
print('columns:', [(c.dataset_urn, c.column, c.change_kind) for c in cs.column_changes])
print('untrusted texts:', len(cs.untrusted_text))
"
```

**Correct output** — one column change, `change_kind` `removed`, dataset URN on
the `dbt` platform.

**Likely failure** — a `ContractViolation` naming the offending field. That is a
`ci/diff` fix, not a `core/` one, and the message says which field.

---

## Step 8 — The full pipeline, both access paths [BOTH]

**Command — SDK path first.** It has no MCP server dependency, so a failure here
is unambiguously in the engine:

```bash
uv run blast-radius analyze --change-set live_change_set.json --out out/report_sdk.json --datahub-mode sdk --no-agent
```

Then the composed path:

```bash
uv run blast-radius analyze --change-set live_change_set.json --out out/report_mcp.json --datahub-mode mcp --no-agent
```

**Correct output**

All 8 stages `ok`, ending with a severity line. `report_mcp.json` must carry
`"access_path": "mcp+sdk"`, `report_sdk.json` `"access_path": "sdk"`.

The two reports should agree on severity. Compare the factor breakdowns:

```bash
uv run python -c "
import json
for name in ('sdk','mcp'):
    r = json.load(open(f'out/report_{name}.json'))
    ci = r['column_impacts'][0]
    print(name, r['datahub']['access_path'], ci['severity']['score'], ci['severity']['level'])
    for f in ci['severity']['factors']:
        print('   ', f['name'], f['raw_value'], f['contribution'])
"
```

**Likely failure**

- **The two paths disagree on score.** The point of the composed reader is that
  they should not. Compare factor by factor; whichever factor differs names the
  read that diverges.
- **Halt at stage 3** with a `DataHubCapabilityError` — the composed reader was
  not used and a bare MCP reader was. Back to step 2.
- **`✗ the generated report does not satisfy its own contract`** — this is the
  `raw_value` bug class. It was fixed in `contracts/loader.py:145`
  (`_prune_optional_nulls`); if it reappears, the message names the field.
- **Severity far below expectation** — check `downstream_reach` and
  `hop_proximity` raw values. Zero downstream means step 4 failure mode 2 got
  through.
- **`critical_consumer` scores 0** — expected until the Looker dashboard and
  mlFeature are seeded. That is the `env/` addition we agreed on; it is why the
  live score will not reach fixture 02's 96.0 without it.

---

## Step 9 — Write the finding back [BOTH]

**Command**

```bash
uv run blast-radius writeback --report out/report_sdk.json --dry-run
uv run blast-radius writeback --report out/report_sdk.json
```

**Correct output**

The dry run prints the record JSON and writes nothing. The real run prints the
write path, one `· impactRecord →` line per dataset, one `· tag` line, and
`✓ wrote the impact record to N dataset(s)`.

Then confirm in the DataHub UI: open `dim_customers`, check the structured
properties panel for `io.blastradius.impactRecord` and the
`blast-radius-critical` tag.

**Likely failure**

- **Structured property definition missing** — same root cause as step 6.
- **`! tag skipped`** — non-fatal by design; the record is the part that
  matters. `core/cli.py:_write_record`.
- **Documentation clobbered** — it must not be. `save_document` merges into a
  delimited block and preserves surrounding text
  (`core/writeback/writer.py:88`, `merge_document`). If a dataset's existing
  documentation vanished, stop and tell OWNER A immediately.

---

## Step 10 — Capture the artifacts [BOTH]

`examples/` is empty and both `docs/JUDGING.md` and the video depend on it.

```bash
cp out/report_mcp.json examples/reports/live_dim_customers_removal.json
```

`examples/reports/` and `examples/fixes/` are OWNER A's subfolders and
`examples/` is append-only — do not rewrite anything already there.

Also keep `lineage_raw.json` from step 4. If the mapping needed changes, that
file becomes the fixture for a regression test so the same shape can never break
again without a test failing first.

---

## What "done" means

| # | Assumption under test | Verified when |
| --- | --- | --- |
| 2 | `mcp` mode builds two readers and reports `mcp+sdk` | Step 2 prints the four expected lines |
| 3 | Tool names match `mcp-server-datahub` 0.6.0 | Step 3 reports no missing tools |
| 4 | `searchAcrossLineage` has the shape `_hops_from_path_nodes` expects | Step 4 shows `paths` with `fieldPath` |
| 5 | Lineage maps to auditable column-level paths | Step 5 prints the three-model chain |
| 6 | Write round-trip works without a token | `doctor` exits 0 |
| 8 | Both access paths agree on a real catalog | Identical scores, different `access_path` |

Anything still unverified after the session stays marked **TODO** in
`docs/JUDGING.md`. A checklist that quietly downgrades an unrun step to "fine"
is the same failure mode this project is about.

---

*Ownership note: `docs/` requires both owners' approval per the table in
[CLAUDE.md](../CLAUDE.md). This file was drafted by OWNER A and needs OWNER B's
review before it counts as agreed.*
