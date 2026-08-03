# Claude Code settings profiles

Two profiles, one per owner. Each blocks reading and editing the other owner's
directories, so an agent's context stays inside its own half of the project.

| File | For | Blocks |
| --- | --- | --- |
| `settings.core.json` | OWNER A (@etka) | `ci/`, `env/`, `.github/workflows/` |
| `settings.ci.json` | OWNER B (@teammate) | `core/`, `skill/` |

## Setup

```bash
cp .claude/settings.core.json .claude/settings.local.json   # OWNER A
cp .claude/settings.ci.json   .claude/settings.local.json   # OWNER B
```

`.claude/settings.local.json` is gitignored, so each developer gets their own
without the two profiles fighting over one tracked file.

**Start Claude Code from the repository root.** Path rules in local settings
anchor at the directory the session was started from, so launching from
`core/` would leave `../ci/` outside the anchored rules.

## Syntax

Verified against the current Claude Code documentation (permissions and settings
reference) at the time of writing, not written from memory. The specifics that
matter here:

- Rules are `ToolName(pattern)`, and `permissions` holds `allow`, `deny`, `ask`
  and `defaultMode`.
- **Only `Read(path)` and `Edit(path)` rules are consulted for file access.** A
  `Write(path)` rule is accepted and then never used, and Claude Code warns
  about it at startup. `Edit(...)` already covers every file-editing tool, so
  these profiles use `Edit` and `Read` only. This is the one thing people get
  wrong when writing these by hand.
- Paths use gitignore syntax. `/ci/**` anchors at the settings source;
  `ci/**` as a *deny* rule matches a `ci` directory at any depth below the
  current directory. Both forms are listed, deliberately, so the rules hold
  whether or not the anchor resolves as expected.
- `deny` always beats `allow`; there is no way to carve an exception out of a
  deny rule. That is why the shared exception types live in `contracts/errors.py`
  rather than `core/errors.py` — OWNER B needs them, and `Read(core/**)` has no
  exemptions to give.
- `ask` rules on `contracts/`, the root docs and `pyproject.toml` are a speed
  bump, not a lock: those files are shared and changing one is an interface
  change that needs the other owner's approval. The prompt is there to make an
  agent stop and mention it.

### Deliberately omitted

`defaultMode` is not set. Its accepted values have changed across Claude Code
versions (`default`/`manual`, `acceptEdits`, `plan`, `auto`, `dontAsk`,
`bypassPermissions`), it is not needed for the isolation these profiles exist
for, and a wrong value would be a startup error rather than a silent no-op.
Set it yourself with `/config` if you want one.

## What these rules do and do not enforce

They are a context-hygiene tool. Be clear-eyed about the boundary:

- **They do stop** the agent's own Read and Edit tools, and file commands
  Claude Code recognises in Bash such as `cat`, `head`, `tail` and `sed`.
- **They do not stop** an arbitrary subprocess. `uv run pytest` reads every
  file in the repository; a Python script that calls `open()` is not
  intercepted. For OS-level enforcement you would need Claude Code's sandbox,
  which is a different feature with different trade-offs.
- **They do not replace review.** `.github/CODEOWNERS` is what actually gates a
  merge. These profiles keep an agent from wandering; CODEOWNERS keeps the
  wandering from landing on `main`.

The real reason for the rules is not security between two teammates who trust
each other. It is that an agent which has read the other half of the project
spends its context on code it must not change, and starts proposing edits across
a boundary that exists to prevent merge conflicts under a seven-day deadline.

## Verifying a profile works

After copying, ask the agent to read a file on the other side. It should refuse:

```
# as OWNER A
"read ci/render/markdown.py"     -> denied by permission rule
```

If it succeeds, check that you started Claude Code from the repository root and
that `.claude/settings.local.json` exists and is valid JSON.
