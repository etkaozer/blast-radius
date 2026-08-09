# Judging criteria → where in this repository

Written for a judge who wants to verify a claim in under a minute, and kept
honest: every gap is marked **TODO** rather than talked around. A scaffold that
overstates itself is the same failure mode this project is about.

**Status as of the current commit: no stubs left, and it has been run against a
live DataHub.** The pipeline completes eight stages against an OSS quickstart and
scores a real pull request at `88.0 critical` — the same 88.0, factor for factor,
through the Python SDK and through the composed `mcp+sdk` reader. The finding is
written back to the catalog. Both reports and the write-back record are in
[`examples/`](../examples/).

The live run is also what found five wrong reads in this repository, each of
which returned a value that scored as a measurement and was not one. They are
fixed. [`docs/LIVE_VERIFICATION.md`](LIVE_VERIFICATION.md) is the ordered
checklist that found them, written so the riskiest assumptions failed first.
`uv run blast-radius stubs` prints the inventory, which is now empty.

---

## Use of DataHub

| Claim | Where | Status |
| --- | --- | --- |
| Both supported access paths behind one interface | `core/datahub/base.py` (protocol), `mcp_client.py`, `sdk_client.py`, `hybrid.py` | Done; both run live, agreeing on 88.0 factor for factor |
| Column-level lineage, N-hop, with full paths | `core/datahub/{mcp_client,sdk_client}.py::get_lineage` | Done; path reconstruction unit tested |
| `get_lineage_paths_between` for "why is this dashboard affected" | both clients | Done; used live to reconstruct hops beyond degree 1 |
| Real query usage, not an estimate | `$defs.queryUsage`, `core/severity/rules.py::normalize_query_usage` | Done, and the two usage sources are kept distinct |
| Assertions and data contracts as severity factors | `core/impact/rules.py`, `core/severity/rules.py` | Done |
| Ownership resolution for notification | `$defs.owner`, both clients | Done |
| Write-back as a structured property | `core/writeback/writer.py`, `uv run blast-radius writeback` | Done on both paths; written to a live catalog and read back |
| Graceful degradation across DataHub deployments | `core/writeback/capabilities.py`, `core/writeback/writer.py::build_writer` | Done and tested |
| `doctor` verifies the write path before anyone depends on it | `core/writeback/doctor.py`, `uv run blast-radius doctor` | Done — writes to a scratch URN, reads back, compares, cleans up |
| Honest about what MCP cannot do | `core/datahub/hybrid.py`, `core/datahub/mcp_client.py` | `mcp-server-datahub` has no data-contract tool and no OSS assertion tool; the composed reader says so rather than scoring zero |

The strongest single claim: DataHub is not decoration here. Every one of the
seven severity factors is a graph fact, and four of them come from DataHub
specifically. Remove DataHub and the tool has no severity score, not a worse one.

---

## Technical Execution

| Claim | Where |
| --- | --- |
| Deterministic core, tested | `core/severity/`, `core/impact/rules.py`, `core/tests/` |
| The model cannot reach severity — proved three ways | `core/tests/test_module_boundaries.py` |
| Pipeline stage order is a control, and is tested | `core/pipeline.py`, `core/tests/test_pipeline_order.py` |
| Typed models validated against JSON Schema in both directions | `contracts/loader.py`, `contracts/tests/test_schemas.py` |
| Content-addressed untrusted-text ids | `contracts/canonical.py`, enforced in schema and model |
| mypy strict, ruff, uv, pre-commit | `pyproject.toml`, `.pre-commit-config.yaml`, `make check` |
| No mock implementations, enforced by test | `core/tests/test_stub_inventory.py` |
| Two-owner isolation that actually holds | `.github/CODEOWNERS`, `.claude/`, per-directory `CLAUDE.md` |
| Generated fixes gated on a compiler | `core/validate/dbt.py` — implemented; the live run passed `--no-agent`, so fix generation is **not yet exercised end to end** |

Run `make check`. It is green.

The end-to-end run against a live DataHub has happened: eight stages green,
`88.0 critical`, both access paths agreeing, the finding written back. What it
cost was five wrong reads, all found by the checklist and all fixed.

**Still not exercised:** fix generation and the `dbt compile` gate, which need an
API key the live run did not use.

---

## Originality

| Claim | Where |
| --- | --- |
| Severity is structurally unreachable by the model, not merely unasked | `core/severity/`, `docs/THREAT_MODEL.md` |
| The prompt-injection threat is treated as a data-catalog problem, not a chatbot one | `docs/THREAT_MODEL.md`, `contracts/fixtures/03_adversarial_description/` |
| Content-addressed prompt delimiters — text cannot close its own envelope | `core/untrusted/envelope.py` |
| Guarantees encoded as schema constants a consumer can rely on | `computed_by`, `effect_on_severity`, `is_heuristic`, `disclaimer` |
| The finding is written back machine-readable, for the next agent | `contracts/writeback_record.schema.json` |
| Generated fixes are compiled before being called fixes | `core/validate/dbt.py` |

What we believe is genuinely new: most "AI reviews your PR with catalog context"
tools put the metadata *in the prompt* and ask the model for a verdict. That
architecture hands the conclusion to whoever can edit a description. Here the
verdict is computed before the model is called, and the model's only jobs are
the two things it is actually better at than a rule engine.

We are not claiming novelty for prompt-injection defence in general, nor for
lineage-based impact analysis. The combination, and specifically making the
guarantee structural and testable, is what we think is worth looking at.

---

## Real-World Usefulness

| Claim | Where |
| --- | --- |
| Runs where the decision is made — the PR | `.github/workflows/blast-radius.yml` |
| Names owners to notify, with the entity each owns | `$defs.owner`, `source` field |
| Shows the full factor breakdown so a team can disagree with a number | `$defs.severityFactor`, `minItems: 7` |
| Reports what it could not measure | `$defs.degradation` |
| Fixes go to a separate branch, never auto-merged | `ci/publish/github.py` |
| Does not block merges by default, and says why | comment at the foot of `blast-radius.yml` |
| Degrades to a useful report without an API key | `--no-agent` |
| Honest limitations, up front | `README.md` |

The design decision we would defend hardest: not failing the build on a critical
finding. A review tool that blocks merges before a team trusts its scoring gets
switched off within a week, and then catches nothing.

**TODO:** no real team has used this yet. Everything above is a design claim
supported by code, not a usage claim supported by evidence.

---

## Submission Quality

| Item | Where | Status |
| --- | --- | --- |
| README written for a 3-minute read | `README.md` | Done |
| Architecture diagram | `README.md` (mermaid) | Done |
| Quickstart that works from a clean clone | `README.md`, `make setup` | Done |
| Honest limitations section | `README.md` | Done |
| One-command demo environment | `env/quickstart.sh`, `env/seed_demo.py` | Done — builds, ingests, seeds governance and usage, and plants the adversarial description |
| 3-minute video script, shot by shot | `docs/DEMO.md` | Done |
| Threat model | `docs/THREAT_MODEL.md` | Done |
| Contributor docs | `CONTRIBUTING.md`, `CONTRACT.md` | Done |
| Apache 2.0, GitHub-detected | `LICENSE` | Done |
| Real generated artifacts to look at | `examples/` | Done — the impact report from both access paths, and the record written to DataHub |

**TODO:** record the video.

---

## Open-source contribution

| Item | Where | Status |
| --- | --- | --- |
| Bug reported to DataHub, found by the live run | [datahub-project/datahub#19016](https://github.com/datahub-project/datahub/issues/19016) | Open, with a minimal reproduction and an offer to PR |
| Plan for a DataHub Skill to upstream | `skill/README.md` | Plan done |
| Draft Skill definition | `skill/SKILL.md` | Draft, format **TODO(verify)** |
| PR to `datahub-project/datahub-skills` | — | **TODO** — not opened |
| Apache 2.0, contribution docs, CODEOWNERS | `LICENSE`, `CONTRIBUTING.md`, `.github/CODEOWNERS` | Done |

The Skill is the part of this that outlives the hackathon: blast-radius is a
GitHub Action for one team, and the Skill is the reusable capability for any
agent with DataHub access.

**The upstream PR is not open yet, and `skill/SKILL.md` is written against our
best understanding of the registry's format rather than a verified one.** Both
are marked in `skill/README.md`. We would rather ship a correct plan and an
unopened PR than an incorrect PR that costs a maintainer their afternoon.

---

## The fastest way to check our claims

```bash
make check                                              # lint, mypy strict, tests
uv run pytest core/tests/test_adversarial_severity.py -v  # prose cannot move a score
uv run pytest core/tests/test_module_boundaries.py -v     # the model cannot reach severity
uv run blast-radius stubs                               # exactly what is not built yet
```

The last command is the one we would run first if we were judging.
