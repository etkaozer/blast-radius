# env/ — OWNER B (@teammate)

**Before editing any file, check the ownership table in the root
[CLAUDE.md](../CLAUDE.md). If the file is outside your scope, stop and tell the
user instead of editing.**

## Scope

The demo environment. Everything a judge needs to see the tool work on a laptop,
and everything the team needs to develop against a real DataHub rather than
against fixtures.

| Path | Responsibility |
| --- | --- |
| `env/quickstart.sh` | Thin wrapper over `datahub docker quickstart` — up, down, status, reset |
| `env/dbt_project/` | A small jaffle-shop-style dbt project with genuinely populated column-level lineage |
| `env/ingestion/` | DataHub ingestion recipes for the dbt project and the warehouse |
| `env/seed_demo.py` | Seeds the demo: ingests metadata, adds owners, assertions, a data contract, usage, and **plants the adversarial description** |

## What this directory may import

- `contracts` — for the shape of anything it writes.
- `contracts.errors` — the shared exception types.
- The standard library, `click`, `pydantic`.
- `acryl-datahub` — for emitting demo metadata.

## What this directory must NOT read

- `core/` — OWNER A's engine, in its entirety
- `skill/` — OWNER A's DataHub Skill

## The demo lineage must be real

The single most important property of `env/dbt_project/`: the column-level
lineage must be genuine, produced by ingesting a project that really does select
these columns from each other. Hand-written lineage aspects would make the demo
work and the product a lie — and a judge who opens DataHub will look.

The demo warehouse mirrors the golden fixtures, so a reviewer can compare what
they see in DataHub against `contracts/fixtures/`:

```
stg_customers ──┬─→ dim_customers ──┬─→ customer_ltv ──→ mart_exec_summary
                │                   ├─→ Revenue Overview (dashboard)
stg_orders   ───┘                   └─→ Customer Health (dashboard)
                                        customer_ltv_bucket (ML feature)
```

## `env/seed_demo.py` and the adversarial description

The seeding script plants the injected description from
`contracts/fixtures/03_adversarial_description/` into the demo dbt project, so
the live demo shows the same attack the tests cover.

Keep the planted text **byte-identical** to the fixture. Its content-addressed
id is derived from a hash of it, and a stray trailing space would produce a
different id, break the correspondence with the fixture, and quietly weaken the
most important part of the demo.

## Rules specific to this directory

1. **`quickstart.sh` wraps, it does not reimplement.** DataHub's own CLI is the
   supported path; the script's job is to make it one command and to fail with
   a useful message when Docker is not running.
2. **Nothing here writes outside `env/`.** No touching the developer's global
   Docker state beyond the DataHub quickstart's own containers, and `reset`
   must say what it is about to delete before it deletes it.
3. **Seeding is idempotent.** Running it twice must not produce two of anything.
4. **Stubs raise, they never fake.** `raise StubNotImplementedError(target,
   OWNER_B, contract)`.

## Where to start

```bash
./env/quickstart.sh up
uv run python env/seed_demo.py
```
