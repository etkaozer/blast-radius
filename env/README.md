# env/ — the demo environment

OWNER B (@teammate). See [CLAUDE.md](CLAUDE.md) for the boundary rules.

Everything a judge needs to see blast-radius work on a laptop, and everything
the team needs to develop against a real DataHub instead of against fixtures.

## Quickstart

```bash
./env/quickstart.sh up          # docker + datahub quickstart, ~5 min on first run
uv run python env/seed_demo.py  # dbt build, ingest, governance metadata, adversarial text
./env/quickstart.sh urls        # where to look
```

Frontend at <http://localhost:9002> (`datahub` / `datahub`), GMS at
<http://localhost:8080>.

```bash
./env/quickstart.sh status      # is GMS answering?
./env/quickstart.sh down        # stop, keep data
./env/quickstart.sh reset       # stop and DELETE all local DataHub data
```

## The demo warehouse

A small jaffle-shop-style dbt project on DuckDB, built so the column-level
lineage is **genuine** — every relationship blast-radius reports is one this
project really creates.

```
raw_customers ──► stg_customers ──┬──► dim_customers ──► customer_ltv ──► mart_exec_summary
raw_orders    ──► stg_orders    ──┘
```

Column paths that matter, and which fixture each one mirrors:

| Path | Fixture |
| --- | --- |
| `stg_customers.email` → `dim_customers.email` → `customer_ltv.customer_email` | `01_rename` |
| `dim_customers.customer_lifetime_value` → `customer_ltv.ltv_usd` → `mart_exec_summary.total_ltv` | `02_removal_contract` |
| `stg_customers.signup_channel` → `dim_customers.signup_channel` → `customer_ltv.acquisition_channel` | `03_adversarial_description` |

The renames in `customer_ltv` are deliberate: a demo where every column keeps
its name all the way down does not show why column-level lineage is worth
having.

`seed_demo.py` adds what a real deployment has and a fresh quickstart does not —
owners, a FIELD assertion, an ACTIVE data contract, and query usage — so the
severity factors have something real to read.

## The adversarial description

`seed_demo.py` overwrites the description of `stg_customers.signup_channel` with
the injected text from `contracts/fixtures/03_adversarial_description/`, so the
live demo shows the same attack the test suite covers.

The repository keeps a benign description in `models/staging/schema.yml`, so
that someone browsing the project does not mistake the attack for our own
documentation. The planted text must stay **byte-identical** to the fixture: its
id is a hash of exactly those bytes.

## Files

| Path | What |
| --- | --- |
| `quickstart.sh` | Wrapper over `datahub docker quickstart` |
| `dbt_project/` | The demo project; `profiles.example.yml` → copy to `profiles.yml` |
| `ingestion/dbt.yml` | Ingestion recipe. `include_column_lineage: true` is the load-bearing line |
| `seed_demo.py` | The seeding sequence |

## If the demo breaks

- **`docker info` fails** — Docker Desktop is not running.
- **Ingestion succeeds but lineage is table-level** — check
  `include_column_lineage` and that `catalog.json` exists; the dbt source needs
  a `dbt docs generate` to produce it.
- **Severity looks lower than the fixture** — query usage was probably not
  seeded. The report will say so in `degradations`; that is the tool being
  honest about a thin catalog, not a bug.
