# The contract

Two people are building this in seven days. They will not sit next to each
other for most of it, and their coding agents will not read each other's code.
The only thing holding the two halves together is `contracts/`.

This document says what is in there, why it is frozen, and what to do when it
has to change anyway.

## The three artifacts

```
              ci/diff                    core/                     ci/render
                 │                         │                           │
   PR diff ─────►│──► change_set.json ────►│──► impact_report.json ───►│──► comment
                 │      (OWNER B →)        │      (← OWNER A)          │
                                           │
                                           └──► writeback_record ──► DataHub
```

| Schema | Produced by | Consumed by | Says |
| --- | --- | --- | --- |
| `contracts/change_set.schema.json` | OWNER B (`ci/diff`) | OWNER A (`core/`) | What the pull request changed |
| `contracts/impact_report.schema.json` | OWNER A (`core/`) | OWNER B (`ci/render`, `ci/publish`) | What it will break, how badly, and what to do |
| `contracts/writeback_record.schema.json` | OWNER A (`core/writeback`) | DataHub, and the next agent | What was found, in machine-readable form |

Alongside them:

- `contracts/models.py` — pydantic models mirroring the schemas, so Python code
  gets types and a handful of invariants the schemas cannot express get enforced
  in one place for both owners.
- `contracts/loader.py` — the only supported way to read or write a payload.
  It validates against the JSON Schema **and** the model, in both directions.
- `contracts/canonical.py` — canonical JSON and content addressing. Both owners
  must hash identically or the untrusted-text envelope becomes forgeable.
- `contracts/errors.py`, `contracts/version.py` — the two things both halves
  need that are not data.
- `contracts/fixtures/` — three complete golden pairs.

## Why the fixtures are frozen

They are the reason neither owner is blocked.

OWNER B writes the extractor to *produce* `contracts/fixtures/01_rename/change_set.json`
and the renderer to *consume* `contracts/fixtures/01_rename/expected_impact_report.json`.
OWNER A writes the engine to turn the first into the second. Both start at hour
one, neither waits, and the day the halves meet, they meet on a file they have
both been testing against all week.

That only works if the fixtures do not move. A fixture that changes on day four
silently invalidates everything the other owner built against it, and they find
out at integration time — which is exactly the moment there is no time left.

So: **fixtures and schemas are frozen after day 1.** Not immutable. Frozen: it
takes a pull request and two approvals, enforced by `.github/CODEOWNERS`.

## Design rules the schemas encode

These are not stylistic. Each one is checked by a test in
`contracts/tests/test_schemas.py`.

**Every object is closed.** `additionalProperties: false` everywhere. An open
object is how a typo in one owner's producer becomes a silently missing field in
the other's consumer.

**The only free-text field is `untrusted_text.value`.** Every other string in
`change_set.schema.json` is an identifier constrained by a pattern, an enum, a
const or a format. The pull request's title and body are deliberately absent
from the `pullRequest` object: they are prose, so they belong in
`untrusted_text` like all other prose. If a plain free-text field ever appears
elsewhere, prose has a path into the engine that does not go through
`core/untrusted`, and the architectural guarantee is gone.

**`severity.computed_by` is the constant `"deterministic"`.** There is no code
path that can write any other value, because the module that constructs a
`Severity` cannot import the agent. The constant makes that claim readable by a
consumer who has not read our source.

**`untrustedFinding.effect_on_severity` is the constant `"none"`** and
`is_heuristic` is the constant `true`. Detection is reporting. It runs after the
score is fixed, and it says so about itself.

**`explanation.disclaimer` is a constant.** The label on model-generated prose
is not the model's to write.

**`severity.factors` has exactly seven entries**, including the ones that
contributed zero, so a reviewer can see what was considered and rejected.

**`untrusted_text.id` is content-addressed** — `"ut-" + sha256(value)[:12]` —
and both the schema pattern and a model validator enforce it. The id becomes the
delimiter nonce around that text in a prompt. If the producer could choose it,
the text could close its own envelope.

## Changing a schema

1. Open an issue or say so in the shared channel *before* writing code. Most
   proposed schema changes are actually a misunderstanding of an existing field.
2. Branch: `work/<your-handle>/contract-<what>`.
3. Change the schema, the model in `contracts/models.py`, and **every affected
   fixture**, in one commit. A schema that does not match its fixtures is worse
   than either alone.
4. Bump `schema_version` if the change is not backward compatible. Adding an
   optional field is compatible. Adding a required field, removing a field,
   narrowing an enum, or changing a meaning is not.
5. `make check` must pass.
6. PR into `main`. CODEOWNERS requires both owners to approve.
7. Tell the other owner it landed. They rebase before they lose an afternoon.

### What counts as backward compatible

| Change | Compatible? |
| --- | --- |
| Add an optional property | Yes |
| Add a value to an enum | No — the other side's exhaustive match breaks |
| Make a required property optional | Yes for consumers, no for producers |
| Make an optional property required | No |
| Loosen a pattern | Yes |
| Tighten a pattern | No |
| Change a description | Yes |
| Change a `const` | No, and think hard: the constants encode guarantees |

## Working against the contract without the other half

Both owners can do everything they need with the fixtures alone.

**OWNER B**, without any of OWNER A's code:

```bash
uv run pytest ci/tests                    # acceptance tests against the fixtures
uv run blast-radius-ci render \
  --report contracts/fixtures/02_removal_contract/expected_impact_report.json \
  --out /tmp/comment.md
```

**OWNER A**, without any of OWNER B's code:

```bash
uv run pytest core/tests
uv run blast-radius analyze \
  --change-set contracts/fixtures/01_rename/change_set.json \
  --out /tmp/report.json
```

Neither command needs the other side to exist. That is the entire point.

## When the contract is wrong

It will be, somewhere. The failure mode to avoid is one owner quietly working
around a gap — adding a field to their own side, inferring something the schema
should have carried, or reading the other half's source to find out how it
behaves. All three produce an integration that works on one machine.

Say the contract is wrong. Fix the contract. It costs one pull request and it is
always cheaper than the alternative.
