# skill/ — OWNER A (@etka)

**Before editing any file, check the ownership table in the root
[CLAUDE.md](../CLAUDE.md). If the file is outside your scope, stop and tell the
user instead of editing.**

## Scope

A DataHub Skill — "breaking change impact analysis" — intended to be upstreamed
as a pull request to `datahub-project/datahub-skills`.

This is the part of the project that outlives the hackathon. blast-radius is a
GitHub Action for one team; the Skill is the reusable capability, usable by any
agent with DataHub access, and the open-source contribution the submission rests
on.

| Path | Responsibility |
| --- | --- |
| `skill/README.md` | The plan: what the Skill does, how it maps onto the registry's format, what we upstream and when |
| `skill/SKILL.md` | The draft skill definition itself |

## What this directory may import

Nothing, currently — it is documentation and a skill definition. If it grows
Python, it may import `contracts` and `core`, and nothing from `ci/` or `env/`.

## What this directory must NOT read

- `ci/` — OWNER B's CI half
- `env/` — OWNER B's demo environment

## Rules specific to this directory

1. **The Skill must stand alone.** Someone installing it from the DataHub skills
   registry has no blast-radius checkout. It cannot import from `core/`, cannot
   assume our contracts exist, and cannot reference our fixture paths.
2. **Verify the registry's format before writing the final version.** The
   structure, frontmatter and naming conventions of
   `datahub-project/datahub-skills` are what the PR has to match — read the
   repository rather than inferring the format from ours. Anything not yet
   verified is marked `TODO(verify)` in `skill/README.md`, and those markers
   are load-bearing: an upstream PR in the wrong format wastes a maintainer's
   time and ours.
3. **Keep the same architectural claim.** The Skill teaches an agent to ground a
   breaking-change review in the metadata graph and to treat description text as
   untrusted. A Skill that tells an agent to read descriptions and judge severity
   would be actively harmful, and shipping one under our name would be worse than
   shipping nothing.
