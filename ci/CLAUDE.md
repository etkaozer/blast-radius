# ci/ — OWNER B (@teammate)

**Before editing any file, check the ownership table in the root
[CLAUDE.md](../CLAUDE.md). If the file is outside your scope, stop and tell the
user instead of editing.**

## Scope

Everything between "a pull request happened" and "the reviewer sees something",
plus everything after "a report exists".

| Package | Responsibility |
| --- | --- |
| `ci/diff/` | `sqlglot`-based deterministic extraction of changed columns; emits `change_set.json` |
| `ci/render/` | `impact_report.json` → markdown PR comment |
| `ci/publish/` | Post the comment, create `fix/blast-radius-<pr>`, commit generated files |
| `ci/cli.py` | `blast-radius-ci extract | render | publish` |

The GitHub Action that drives all three lives in
`.github/workflows/blast-radius.yml`, which is also yours.

## What this directory may import

- `contracts` — the frozen interface, the shared exception types
  (`contracts.errors`) and the version string (`contracts.version`). Always.
- The standard library.
- `sqlglot`, `click`, `pydantic` — declared dependencies.

## What this directory must NOT read

Do not open, grep or reason about:

- `core/` — OWNER A's engine, in its entirety
- `skill/` — OWNER A's DataHub Skill

If you need to know what OWNER A consumes or produces, read
`contracts/change_set.schema.json`, `contracts/impact_report.schema.json` and
the golden fixtures in `contracts/fixtures/`. Your acceptance tests in
`ci/tests/` are written against those fixtures, so you are never blocked on
OWNER A's code existing.

## Rules specific to this directory

1. **No LLM in `ci/diff/`. Ever.** Which columns changed is a fact about two
   revisions of a file. It is the ground truth a severity score is computed
   from, and a tool that guesses at it cannot be that.
2. **Collect untrusted text verbatim.** Descriptions, `meta` blocks and SQL
   comments are copied exactly — not stripped, not escaped, not normalised, not
   truncated. Stamp `id` with `contracts.canonical.untrusted_id(value)`;
   the id is the delimiter nonce OWNER A binds a prompt envelope with, and a
   sequential id would make that envelope forgeable. Whatever you strip here can
   never be reported later, and reporting it is the point of the project.
3. **Render safely, quote faithfully.** In `ci/render/`, untrusted text is
   quoted so it cannot alter the comment's structure or @-mention anyone — and
   its content is preserved so a human can read exactly what was written.
4. **Never present an unverified fix as verified.** `validation.passed` is
   false for fixes that did not compile; those go in a separate, collapsed
   section, and they never reach the fix branch.
5. **Publishing is idempotent.** Update the existing comment (found by
   `COMMENT_MARKER`) instead of posting a new one; force-push only to
   `fix/blast-radius-<pr>`, never to the PR's own branch.
6. **Stubs raise, they never fake.** `raise StubNotImplementedError(target,
   OWNER_B, contract)` with a docstring describing the intended behaviour.

## Where to start

```bash
uv run blast-radius stubs --owner B
uv run pytest ci/tests            # your acceptance tests, currently xfail
```

The tests in `ci/tests/` are `xfail(strict=True)`. When you implement a stub,
the corresponding test either passes for real — at which point strict xfail
turns the pass into a failure and you delete the marker — or it fails for a real
reason. The markers cannot rot.
