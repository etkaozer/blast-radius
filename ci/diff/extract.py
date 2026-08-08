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

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import sqlglot
import yaml
from sqlglot import exp

from ci.diff.dbt import DatasetRef, DbtProject, DbtProjectNotFoundError
from contracts.canonical import untrusted_id
from contracts.errors import BlastRadiusError
from contracts.loader import to_payload, validate_instance
from contracts.models import (
    ChangeKind,
    ChangeSet,
    ColumnChange,
    Extractor,
    PullRequestRef,
    UntrustedSource,
    UntrustedText,
)
from contracts.version import VERSION

#: Marker returned by `parse_projection` for `SELECT *`. A star projection
#: cannot be resolved to column names from the file alone, and reporting an
#: empty projection instead would look exactly like a model with no columns.
STAR: Final[str] = "*"
STAR_PROJECTION: Final[tuple[tuple[str, str | None], ...]] = ((STAR, None),)

#: Key under which the YAML loader records the source line of each mapping key.
_LINES: Final[str] = "__lines__"

_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.DOTALL)
_JINJA_STATEMENT = re.compile(r"\{%.*?%\}", re.DOTALL)
_JINJA_CONFIG = re.compile(r"\{\{\s*config\s*\(.*?\)\s*\}\}", re.DOTALL)
_JINJA_EXPRESSION = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
_LINE_COMMENT = re.compile(r"^\s*--\s?(.*)$")
_BLOCK_COMMENT = re.compile(r"/\*(.*?)\*/", re.DOTALL)


class UnresolvableProjectionError(BlastRadiusError):
    """A model's projection cannot be resolved to a set of named columns.

    Raised rather than returned, because every caller downstream of here treats
    "no columns changed" as "nothing to review". A star select or a file sqlglot
    cannot parse is not that: it is a file the tool could not read, and the
    reviewer needs to be told the difference.
    """


@dataclass(frozen=True, slots=True)
class FileDiff:
    """One changed file, as two revisions plus its path."""

    path: str
    base_content: str | None
    head_content: str | None


@dataclass(frozen=True, slots=True)
class _Projected:
    """One projected column, with the evidence rename inference needs."""

    name: str
    declared_type: str | None
    expression: str
    is_identity: bool
    position: int


# --------------------------------------------------------------------------
# jinja
# --------------------------------------------------------------------------


def _placeholder(inner: str) -> str:
    """Return a stable identifier standing in for a jinja expression.

    Derived from the expression's own text, so `{{ ref('raw_customers') }}`
    becomes the same identifier in both revisions of a file. That is what lets
    the diff compare expressions across revisions rather than comparing
    placeholders that happen to be numbered differently.
    """
    digest = hashlib.sha256(inner.strip().encode("utf-8")).hexdigest()[:8]
    return f"_j{digest}"


def neutralise_jinja(sql: str) -> str:
    """Replace dbt jinja with parseable placeholders, preserving column positions.

    `{{ config(...) }}`, `{% ... %}` and `{# ... #}` render to nothing and are
    removed. Every other `{{ ... }}` stands where an identifier stands — a table
    in a `from`, a value in a projection — and becomes one, so that the
    surrounding SQL still parses and the projection keeps its shape.
    """
    # A model authored on Windows can start with a UTF-8 BOM. sqlglot does not
    # skip it, and the parse fails at the first statement with a position that
    # points at valid SQL -- which reads as a broken model rather than as an
    # encoding artefact. Strip it here, where every path into the parser passes.
    sql = sql.lstrip("\ufeff")
    sql = _JINJA_COMMENT.sub("", sql)
    sql = _JINJA_STATEMENT.sub("", sql)
    sql = _JINJA_CONFIG.sub("", sql)
    return _JINJA_EXPRESSION.sub(lambda m: _placeholder(m.group(1)), sql)


# --------------------------------------------------------------------------
# projection
# --------------------------------------------------------------------------


def _select_of(sql: str, dialect: str) -> exp.Select:
    try:
        tree = sqlglot.parse_one(neutralise_jinja(sql), read=dialect)
    except Exception as exc:  # sqlglot raises several types for a bad parse
        msg = f"sqlglot could not parse the model: {exc}"
        raise UnresolvableProjectionError(msg) from exc

    select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    if select is None:
        msg = "the model does not contain a SELECT"
        raise UnresolvableProjectionError(msg)
    return select


def _is_star(expression: exp.Expression) -> bool:
    return isinstance(expression, exp.Star) or (
        isinstance(expression, exp.Column) and isinstance(expression.this, exp.Star)
    )


def _project(sql: str, dialect: str) -> tuple[_Projected, ...]:
    """Return the outermost SELECT's projections, with rename evidence attached."""
    select = _select_of(sql, dialect)
    projected: list[_Projected] = []

    for position, item in enumerate(select.expressions):
        if _is_star(item):
            return ()

        inner = item.this if isinstance(item, exp.Alias) else item
        name = item.alias_or_name
        if not name:
            msg = f"projection {position} has no output name: {item.sql(dialect=dialect)}"
            raise UnresolvableProjectionError(msg)

        declared_type = (
            inner.to.sql(dialect=dialect).upper() if isinstance(inner, exp.Cast) else None
        )
        projected.append(
            _Projected(
                name=name,
                declared_type=declared_type,
                expression=inner.sql(dialect=dialect),
                is_identity=isinstance(inner, exp.Column),
                position=position,
            )
        )

    return tuple(projected)


def parse_projection(sql: str, dialect: str = "snowflake") -> tuple[tuple[str, str | None], ...]:
    """Return the (column, declared type) pairs a dbt model projects.

    Aliases resolve to their output names, because the output name is what
    downstream models reference. A declared type is reported only where the SQL
    actually declares one — a `cast(... as date)` — and is None otherwise;
    warehouse types come from the dbt catalog, not from the model file.

    `SELECT *` returns `STAR_PROJECTION` rather than an empty tuple. The caller
    turns that into a `column_level_lineage` degradation; silently reporting no
    columns for a star select is the worst available failure.
    """
    projected = _project(sql, dialect)
    if not projected:
        return STAR_PROJECTION
    return tuple((item.name, item.declared_type) for item in projected)


# --------------------------------------------------------------------------
# diff
# --------------------------------------------------------------------------


def _line_of(content: str | None, column: str) -> int | None:
    """Return the 1-based line where `column` first appears, for a source link."""
    if not content:
        return None
    pattern = re.compile(rf"\b{re.escape(column)}\b")
    for number, line in enumerate(content.splitlines(), start=1):
        if pattern.search(line):
            return number
    return None


def _values_for(
    kind: ChangeKind, base: _Projected | None, head: _Projected | None
) -> tuple[str | None, str | None]:
    """Return (old_value, new_value) for a change of `kind`.

    `renamed` carries the two names, because the names are what changed.
    Everything else carries the two types — falling back to the projected
    expression where the model file declares no type, so the field says what was
    there rather than being absent.
    """
    if kind == "renamed":
        assert base is not None and head is not None
        return base.name, head.name
    old = None if base is None else (base.declared_type or base.expression)
    new = None if head is None else (head.declared_type or head.expression)
    return old, new


def _infer_rename(
    removed: list[_Projected], added: list[_Projected]
) -> tuple[_Projected, _Projected] | None:
    """Pair a removal with an addition when the evidence says it is a rename.

    Rename is inferred, never observed. The evidence required is deliberately
    narrow: exactly one unpaired removal and one unpaired addition, at the same
    position in the projection, with the same declared type, and either the same
    underlying expression or an identity projection on both sides.

    When the evidence is weaker than that the caller reports add + remove, which
    is noisier and safer: a false rename produces a fix that silently changes
    semantics, while a false add+remove produces a fix that fails to compile.
    """
    if len(removed) != 1 or len(added) != 1:
        return None
    before, after = removed[0], added[0]
    if before.position != after.position or before.declared_type != after.declared_type:
        return None
    if before.expression == after.expression or (before.is_identity and after.is_identity):
        return before, after
    return None


def diff_columns(file_diff: FileDiff, dialect: str = "snowflake") -> tuple[ColumnChange, ...]:
    """Compute the column changes between two revisions of one model file.

    `removed` when a column is in base and not in head, `added` for the reverse,
    `type_changed` when the declared type differs, and `renamed` when
    `_infer_rename` finds enough evidence to pair a removal with an addition.

    Ids are `cc-1`, `cc-2`, … in file order and are stable for a stable input,
    so re-running on a pushed commit produces comparable reports.

    `dataset_urn` and `dataset_name` come from the dbt project — the compiled
    manifest where there is one, `dbt_project.yml` otherwise — never from the
    filename. See `ci/diff/dbt.py`.
    """
    return _changes_for(file_diff, _resolve_dataset(file_diff), dialect, start_id=1)


def _resolve_dataset(file_diff: FileDiff, project: DbtProject | None = None) -> DatasetRef:
    model = Path(file_diff.path).stem
    resolved = project or DbtProject.discover(file_diff.path)
    return resolved.dataset_for(model, file_diff.path)


def _changes_for(
    file_diff: FileDiff, dataset: DatasetRef, dialect: str, start_id: int
) -> tuple[ColumnChange, ...]:
    model = Path(file_diff.path).stem
    base = _project(file_diff.base_content, dialect) if file_diff.base_content else ()
    head = _project(file_diff.head_content, dialect) if file_diff.head_content else ()

    if file_diff.base_content and not base:
        msg = f"{file_diff.path}: the base revision projects `SELECT *`"
        raise UnresolvableProjectionError(msg)
    if file_diff.head_content and not head:
        msg = f"{file_diff.path}: the head revision projects `SELECT *`"
        raise UnresolvableProjectionError(msg)

    base_by_name = {item.name: item for item in base}
    head_by_name = {item.name: item for item in head}

    removed = [item for item in base if item.name not in head_by_name]
    added = [item for item in head if item.name not in base_by_name]

    pending: list[tuple[int, ChangeKind, _Projected | None, _Projected | None]] = []

    rename = _infer_rename(removed, added)
    if rename is not None:
        before, after = rename
        pending.append((after.position, "renamed", before, after))
    else:
        pending.extend((item.position, "removed", item, None) for item in removed)
        pending.extend((item.position, "added", None, item) for item in added)

    for name, existing in base_by_name.items():
        updated = head_by_name.get(name)
        if updated is not None and existing.declared_type != updated.declared_type:
            pending.append((updated.position, "type_changed", existing, updated))

    changes: list[ColumnChange] = []
    for offset, (_, kind, old, new) in enumerate(
        sorted(pending, key=lambda row: (row[0], row[1])), start=start_id
    ):
        subject = old if kind in {"removed", "renamed"} else new
        assert subject is not None
        old_value, new_value = _values_for(kind, old, new)
        content = file_diff.base_content if kind == "removed" else file_diff.head_content
        lookup = subject.name if kind != "renamed" else (new.name if new else subject.name)
        changes.append(
            ColumnChange(
                id=f"cc-{offset}",
                dbt_model=model,
                dataset_urn=dataset.urn,
                dataset_name=dataset.name,
                column=subject.name,
                change_kind=kind,
                old_value=old_value,
                new_value=new_value,
                file_path=file_diff.path,
                line=_line_of(content, lookup),
            )
        )

    return tuple(changes)


# --------------------------------------------------------------------------
# untrusted text
# --------------------------------------------------------------------------


class _LineLoader(yaml.SafeLoader):
    """A SafeLoader that records the source line of every mapping key."""


def _construct_mapping(loader: _LineLoader, node: yaml.MappingNode) -> dict[str, Any]:
    mapping: dict[str, Any] = {
        str(key): value for key, value in loader.construct_mapping(node, deep=True).items()
    }
    mapping[_LINES] = {
        str(key.value): key.start_mark.line + 1
        for key, _ in node.value
        if isinstance(key, yaml.ScalarNode)
    }
    return mapping


_LineLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def _line_for(mapping: dict[str, Any], key: str) -> int | None:
    lines = mapping.get(_LINES)
    return lines.get(key) if isinstance(lines, dict) else None


def _sql_comments(file_diff: FileDiff) -> list[tuple[str, int]]:
    """Return (text, line) for each comment in the head revision, verbatim.

    Consecutive `--` lines are one comment, because that is how the person who
    wrote them meant them to be read, and an instruction split across two lines
    is not two instructions.
    """
    content = file_diff.head_content or file_diff.base_content
    if not content:
        return []

    comments: list[tuple[str, int]] = []
    block: list[str] = []
    block_start = 0

    for number, line in enumerate(content.splitlines(), start=1):
        match = _LINE_COMMENT.match(line)
        if match:
            if not block:
                block_start = number
            block.append(match.group(1))
            continue
        if block:
            comments.append(("\n".join(block), block_start))
            block = []
    if block:
        comments.append(("\n".join(block), block_start))

    for match in _BLOCK_COMMENT.finditer(content):
        line_number = content[: match.start()].count("\n") + 1
        comments.append((match.group(1), line_number))

    return [(text, at) for text, at in comments if text.strip()]


def _resolve_schema_path(schema_yml: Path | None, model_path: str) -> Path | None:
    """Resolve a project-relative schema.yml path against the dbt project.

    Paths in a change set are project-relative — `models/staging/schema.yml` —
    because that is what a dbt diff and a DataHub URN both speak. Resolving them
    means finding the project, which is the same lookup the dataset resolver
    does.
    """
    if schema_yml is None:
        return None
    if schema_yml.is_file():
        return schema_yml
    try:
        project = DbtProject.discover(model_path)
    except DbtProjectNotFoundError:
        return None
    candidate = project.root / schema_yml
    return candidate if candidate.is_file() else None


def _model_entry(document: Any, model: str) -> dict[str, Any] | None:
    models = document.get("models") if isinstance(document, dict) else None
    if not isinstance(models, list):
        return None
    for entry in models:
        if isinstance(entry, dict) and entry.get("name") == model:
            return entry
    return None


def _flatten_meta(meta: dict[str, Any]) -> str:
    return "; ".join(f"{key}: {value}" for key, value in meta.items() if key != _LINES)


def _text(
    field: str,
    source: UntrustedSource,
    value: str,
    file_path: str | None,
    line: int | None,
) -> UntrustedText:
    """Build an UntrustedText, content-addressing its id from the value itself."""
    return UntrustedText(
        id=untrusted_id(value),
        field=field,
        source=source,
        value=value,
        file_path=file_path,
        line=line,
    )


def collect_untrusted_text(
    file_diff: FileDiff, schema_yml: Path | None
) -> tuple[UntrustedText, ...]:
    """Collect every free-text field the diff touched, VERBATIM.

    Descriptions, `meta` values and SQL comments are copied exactly as written —
    not stripped, not escaped, not normalised, not truncated. Whatever is
    dropped here can never be reported later, and the misleading description
    reaching the report intact is the point of the project.

    Ids are stamped with `contracts.canonical.untrusted_id(value)`. The id is
    the delimiter nonce OWNER A binds a prompt envelope with; a sequential id
    would make that envelope forgeable.
    """
    model = Path(file_diff.path).stem
    collected: list[UntrustedText] = [
        _text(f"models.{model}.sql_comment.{line}", "sql_comment", value, file_diff.path, line)
        for value, line in _sql_comments(file_diff)
    ]

    resolved = _resolve_schema_path(schema_yml, file_diff.path)
    if resolved is not None:
        declared = str(schema_yml)
        document = yaml.load(resolved.read_text(encoding="utf-8"), Loader=_LineLoader)
        entry = _model_entry(document, model)
        if entry is not None:
            description = entry.get("description")
            if isinstance(description, str):
                collected.append(
                    _text(
                        f"models.{model}.description",
                        "dbt_yaml_description",
                        description,
                        declared,
                        _line_for(entry, "description"),
                    )
                )
            collected.extend(_column_text(file_diff, entry, model, declared))

    seen: set[str] = set()
    unique: list[UntrustedText] = []
    for text in collected:
        if text.id not in seen:
            seen.add(text.id)
            unique.append(text)
    return tuple(unique)


def _column_text(
    file_diff: FileDiff, entry: dict[str, Any], model: str, declared: str
) -> list[UntrustedText]:
    """Collect descriptions and meta for the columns this diff actually touched."""
    touched = _touched_column_names(file_diff)
    columns = entry.get("columns")
    if not isinstance(columns, list):
        return []

    texts: list[UntrustedText] = []
    for column in columns:
        if not isinstance(column, dict):
            continue
        name = column.get("name")
        if not isinstance(name, str) or (touched and name not in touched):
            continue

        description = column.get("description")
        if isinstance(description, str):
            texts.append(
                _text(
                    f"models.{model}.columns.{name}.description",
                    "dbt_yaml_description",
                    description,
                    declared,
                    _line_for(column, "description"),
                )
            )

        meta = column.get("meta")
        if isinstance(meta, dict):
            flattened = _flatten_meta(meta)
            if flattened:
                texts.append(
                    _text(
                        f"models.{model}.columns.{name}.meta",
                        "dbt_yaml_meta",
                        flattened,
                        declared,
                        _line_for(column, "meta"),
                    )
                )
    return texts


def _touched_column_names(file_diff: FileDiff, dialect: str = "snowflake") -> set[str]:
    """Return the column names that differ between the two revisions.

    Deliberately independent of `diff_columns`: collecting text must not depend
    on a dataset URN resolving, or a project without a compiled manifest would
    lose the descriptions as well as the identity.
    """
    try:
        base = _project(file_diff.base_content, dialect) if file_diff.base_content else ()
        head = _project(file_diff.head_content, dialect) if file_diff.head_content else ()
    except UnresolvableProjectionError:
        return set()

    base_names = {item.name for item in base}
    head_names = {item.name for item in head}
    changed = base_names ^ head_names
    changed |= {
        item.name
        for item in head
        if item.name in base_names
        and item.declared_type != next(b.declared_type for b in base if b.name == item.name)
    }
    return changed


# --------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------


def _schema_yml_for(model_path: str) -> Path:
    """dbt's convention: the schema file sits beside the models it documents."""
    return Path(model_path).parent / "schema.yml"


def build_change_set(
    pull_request: PullRequestRef,
    file_diffs: tuple[FileDiff, ...],
    manifest_path: Path | None = None,
) -> ChangeSet:
    """Assemble the full ChangeSet for a pull request.

    Column-scoped text travels with its column; text about the model as a whole
    stays at change-set level. Ids are assigned across all files so that `cc-3`
    means one thing in a report, not one thing per file.

    The result is validated against `contracts/change_set.schema.json` before it
    is returned, so an invalid change set never reaches OWNER A's half.
    """
    if not file_diffs:
        msg = "no changed files were supplied; there is nothing to extract"
        raise BlastRadiusError(msg)

    project = _project_for(file_diffs, manifest_path)
    changes: list[ColumnChange] = []
    shared: list[UntrustedText] = []

    for file_diff in file_diffs:
        dataset = _resolve_dataset(file_diff, project)
        file_changes = _changes_for(file_diff, dataset, "snowflake", start_id=len(changes) + 1)
        texts = collect_untrusted_text(file_diff, _schema_yml_for(file_diff.path))
        changes.extend(_attach(file_changes, texts))
        shared.extend(text for text in texts if ".columns." not in text.field)

    if not changes:
        msg = "no column changes were found in the supplied diffs"
        raise BlastRadiusError(msg)

    change_set = ChangeSet(
        pull_request=pull_request,
        column_changes=tuple(changes),
        untrusted_text=tuple(_unique(shared)),
        extracted_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        extractor=Extractor(
            name="blast-radius-ci",
            version=VERSION,
            method="hybrid" if project is not None and project.manifest_path else "sqlglot",
        ),
    )
    validate_instance(to_payload(change_set), "change_set")
    return change_set


def _project_for(file_diffs: tuple[FileDiff, ...], manifest_path: Path | None) -> DbtProject | None:
    """Locate the dbt project once for the whole change set."""
    if manifest_path is not None and manifest_path.is_file():
        return DbtProject.at(manifest_path.parent.parent, manifest_path)
    try:
        return DbtProject.discover(file_diffs[0].path)
    except DbtProjectNotFoundError:
        return None


def _attach(
    changes: tuple[ColumnChange, ...], texts: tuple[UntrustedText, ...]
) -> list[ColumnChange]:
    """Give each change the text written about its own column."""
    attached: list[ColumnChange] = []
    for change in changes:
        names = {change.column}
        if change.change_kind == "renamed" and change.new_value:
            names.add(change.new_value)
        owned = tuple(
            text for text in texts if any(f".columns.{name}." in text.field for name in names)
        )
        attached.append(change.model_copy(update={"untrusted_text": owned}))
    return attached


def _unique(texts: list[UntrustedText]) -> list[UntrustedText]:
    seen: set[str] = set()
    unique: list[UntrustedText] = []
    for text in texts:
        if text.id not in seen:
            seen.add(text.id)
            unique.append(text)
    return unique
