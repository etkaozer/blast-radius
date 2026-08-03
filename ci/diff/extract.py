"""Deterministic extraction of changed columns from a dbt model diff.

No LLM. Not "no LLM for now" — no LLM by contract. The set of columns a pull
request changes is a fact about two revisions of a SQL file, and a tool that
guesses at it cannot be the ground truth for a severity score. `sqlglot` parses
both revisions; the diff is set arithmetic over the resulting projections.

Output is a `ChangeSet` (contracts/change_set.schema.json), which OWNER A
consumes. The golden fixtures in `contracts/fixtures/` are the acceptance test:
if this module can reproduce them from the corresponding diffs, the interface
holds.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from contracts.errors import OWNER_B, StubNotImplementedError
from contracts.models import ChangeSet, ColumnChange, PullRequestRef, UntrustedText

_T = "ci.diff.extract"


@dataclass(frozen=True, slots=True)
class FileDiff:
    """One changed file, as two revisions plus its path."""

    path: str
    base_content: str | None
    head_content: str | None


def parse_projection(sql: str, dialect: str = "snowflake") -> tuple[tuple[str, str | None], ...]:
    """Return the (column, declared type) pairs a dbt model projects.

    Contract:

    - Use `sqlglot.parse_one(sql, dialect=dialect)` and read the outermost
      SELECT's projections. Resolve aliases to their output names, since the
      output name is what downstream models reference.
    - dbt jinja (`{{ ref(...) }}`, `{{ config(...) }}`) must be neutralised
      before parsing — replace with a placeholder identifier rather than
      stripping, so column positions stay meaningful.
    - `SELECT *` cannot be resolved from the file alone. Return a marker the
      caller turns into a `column_level_lineage` degradation instead of
      pretending the projection is empty; silently reporting no columns for a
      star select is the worst available failure.
    - Deterministic and side-effect free.
    """
    raise StubNotImplementedError(
        f"{_T}.parse_projection",
        OWNER_B,
        "sqlglot parse of a dbt model to (column, type) pairs, jinja-neutralised, star-aware",
    )


def diff_columns(file_diff: FileDiff, dialect: str = "snowflake") -> tuple[ColumnChange, ...]:
    """Compute the column changes between two revisions of one model file.

    Contract:

    - `removed` when a column is in base and not in head; `added` for the
      reverse; `type_changed` when the declared type differs.
    - `renamed` is inferred, not observed: a same-position, same-expression
      column with a different output name is a rename rather than an
      add+remove pair. Getting this wrong is expensive in both directions, so
      prefer reporting add+remove when the evidence is weak — a false rename
      generates a fix that silently changes semantics.
    - Ids are assigned `cc-1`, `cc-2`, … in file order, and must be stable for
      a stable input so that re-running on a pushed commit produces comparable
      reports.
    - Populates `dataset_urn` and `dataset_name` from the dbt manifest, not by
      string-building a URN from a filename.
    """
    raise StubNotImplementedError(
        f"{_T}.diff_columns",
        OWNER_B,
        "set-difference the two projections into removed/added/type_changed/renamed ColumnChanges",
    )


def collect_untrusted_text(
    file_diff: FileDiff, schema_yml: Path | None
) -> tuple[UntrustedText, ...]:
    """Collect every free-text field the diff touched, VERBATIM.

    Contract, and the one to get right:

    - Copy descriptions, `meta` values, docs blocks and SQL comments exactly as
      written. Do NOT strip, escape, normalise whitespace or truncate. Whatever
      is stripped here cannot be reported later, and the whole point of this
      project is that the misleading description reaches the report intact.
    - Stamp `id` with `contracts.canonical.untrusted_id(value)`. The id is the
      delimiter nonce OWNER A binds a prompt envelope with; assigning a
      sequential id instead would make the envelope forgeable.
    - Record `file_path` and `line` so the PR comment can link to the source.
    """
    raise StubNotImplementedError(
        f"{_T}.collect_untrusted_text",
        OWNER_B,
        "verbatim collection of descriptions/meta/comments with content-addressed ids",
    )


def build_change_set(
    pull_request: PullRequestRef,
    file_diffs: tuple[FileDiff, ...],
    manifest_path: Path | None = None,
) -> ChangeSet:
    """Assemble the full ChangeSet for a pull request.

    Contract: validate the result with `contracts.loader.dump(..., 'change_set')`
    before returning, so an invalid change set never reaches OWNER A's half.
    """
    raise StubNotImplementedError(
        f"{_T}.build_change_set",
        OWNER_B,
        "assemble and schema-validate the ChangeSet from per-file diffs",
    )
