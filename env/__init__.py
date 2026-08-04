"""The demo environment. OWNER B (teammate).

| module | role |
| --- | --- |
| `env.seed_demo` | Builds the dbt project, ingests it, plants the adversarial description |
| `env.schema_yaml` | Targeted edits to a dbt `schema.yml` that preserve everything else |

A package rather than a pair of loose scripts because `seed_demo` imports
`schema_yaml`, and a module that is importable under two names is a module mypy
refuses to check.

Imports allowed: `contracts`, the standard library, `click`, `pydantic` and
`acryl-datahub`. Nothing from `core/` or `skill/`.
"""
