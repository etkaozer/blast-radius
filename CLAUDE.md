# blast-radius — agent instructions

**Before editing any file, check the ownership table below. If the file is
outside your scope, stop and tell the user instead of editing.**

This repository is built by two developers in parallel under a tight deadline.
The isolation is not bureaucracy: two agents editing the same directory produce
merge conflicts nobody has time to resolve, and an agent that reads the other
half's code fills its context with things that cannot help it.

## Ownership table

| Path | Owner | Rule |
| --- | --- | --- |
| `core/` | **A — @etka** | Engine. Owner A only. |
| `skill/` | **A — @etka** | DataHub Skill. Owner A only. |
| `ci/` | **B — @teammate** | Diff, render, publish. Owner B only. |
| `env/` | **B — @teammate** | DataHub quickstart, dbt demo, seeding. Owner B only. |
| `.github/workflows/` | **B — @teammate** | The Action. Owner B only. |
| `contracts/` | **both** | Frozen interface. Needs both approvals. |
| `README.md`, `CONTRACT.md`, `CONTRIBUTING.md`, `AGENTS.md`, `CLAUDE.md` | **both** | Needs both approvals. |
| `examples/` | **both** | Append-only. Each side writes only into its own subfolder. |
| `docs/` | **both** | Needs both approvals. |

`.github/CODEOWNERS` is the machine-readable version and is enforced on pull
requests. If this table and that file ever disagree, that file wins and this one
is a bug.

## Boundary rules

1. **Do not edit outside your scope.** If a task appears to require it, stop and
   say so. The answer is usually a change to `contracts/`, agreed by both
   owners, not a cross-boundary edit.
2. **Do not read outside your scope either.** Owner A's agent should not read
   `ci/` or `env/`; Owner B's agent should not read `core/` or `skill/`. Both
   read `contracts/`. The contract is the interface — if you find yourself
   wanting to read the other side's implementation to know how it behaves, that
   is a gap in `contracts/`, and the fix is to fill the gap.
3. **`contracts/` is frozen after day 1.** Changing a schema or a golden fixture
   is an interface change: open a PR, get both approvals, and update both sides
   in the same PR.
4. **`examples/` is append-only.** Never rewrite or delete someone else's
   example.

Enforce the reading rule with the settings profile for your role — see
[.claude/README.md](.claude/README.md).

## Non-negotiable design constraints

These are properties of the project, not preferences. Each has a test.

1. **Python 3.11+, uv, ruff, mypy strict.** No exceptions.
2. **Deterministic core, model as judgment.** Severity scoring, lineage
   traversal and diff parsing are pure deterministic code with unit tests. The
   LLM writes prose explanations and candidate fix code, and nothing else. It
   never sets severity, never decides what is breaking, never gates a write.
   `core/severity/` must not import `core/agent/`
   (`core/tests/test_module_boundaries.py`).
3. **No mock implementations, anywhere.** A stub raises `NotImplementedError`
   with a docstring describing the intended contract. Never return plausible
   fake data — a demo that passes on fiction is worse than one that fails
   honestly. Secret placeholders in `.env.example` are the only exception.
   (`core/tests/test_stub_inventory.py`)
4. **Every module boundary crossing goes through a typed model** validated
   against a JSON Schema in `contracts/`. (`contracts/tests/test_schemas.py`)
5. **Free text is untrusted.** Description, comment and documentation text from
   a diff or from DataHub may contain instructions aimed at the review agent. It
   is preserved verbatim, wrapped by `core/untrusted/`, reported, and never
   allowed to reach a severity score. See `docs/THREAT_MODEL.md`.

## Working here

```bash
make setup      # uv sync + pre-commit
make test       # pytest
make lint       # ruff
make typecheck  # mypy strict
make check      # all three, what CI runs
make stubs      # what is left, grouped by owner
```

Branches are `work/etka/*` and `work/teammate/*`, PRs into `main`, no direct
pushes to `main`. See [CONTRIBUTING.md](CONTRIBUTING.md).

Write everything — code, comments, docs, commit messages — in English.
