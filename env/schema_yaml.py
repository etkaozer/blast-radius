"""Editing a dbt `schema.yml` in place, without rewriting the parts we did not mean to.

`env/seed_demo.py` has to plant a specific description into
`models/staging/schema.yml` byte for byte. Loading the file with a YAML parser
and dumping it back would do that — and would also drop every comment in the
file, including the one that tells a reader browsing the repository that the
adversarial text is a demo fixture rather than our own documentation. Losing
that comment would be a small disaster in a project whose whole subject is
misleading text in a catalog.

So this module edits the source text: it finds one column's block, replaces
`description:` and `meta:` inside it, and leaves every other byte alone. The
result is then parsed and checked against what was asked for, because a
hand-rolled editor that silently produced the wrong bytes would break the
correspondence between the demo and the fixture, and nothing else would notice.
"""

from __future__ import annotations

import re
from typing import Final

import yaml

from contracts.errors import BlastRadiusError

#: Two spaces per level, which is what the demo project uses and what dbt's own
#: documentation shows. The editor reads the file's real indentation for the
#: block it edits; this is only the step it adds inside that block.
_STEP: Final[int] = 2


class SchemaEditError(BlastRadiusError):
    """A `schema.yml` could not be edited as asked."""


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _block_scalar(value: str, indent: int) -> list[str]:
    """Render `value` as a `|-` block scalar that round-trips byte for byte.

    `|-` strips the trailing newline, so a value with no trailing newline —
    which is what the fixture holds — survives a write-then-read cycle. A
    folded scalar would not: it rewrites newlines as spaces.
    """
    pad = " " * (indent + _STEP)
    return [f"{' ' * indent}description: |-"] + [f"{pad}{line}" for line in value.split("\n")]


def _mapping(key: str, values: dict[str, str], indent: int) -> list[str]:
    pad = " " * (indent + _STEP)
    return [f"{' ' * indent}{key}:"] + [f"{pad}{name}: {value}" for name, value in values.items()]


def _column_span(lines: list[str], column: str) -> tuple[int, int, int]:
    """Return (start, end, indent) of one column's block in a dbt schema.yml.

    `start` is the `- name: <column>` line; `end` is one past the last line
    belonging to it; `indent` is the indentation of the keys inside it.
    """
    marker = re.compile(rf"^(\s*)-\s+name:\s*{re.escape(column)}\s*$")
    for index, line in enumerate(lines):
        match = marker.match(line)
        if not match:
            continue
        dash_indent = len(match.group(1))
        key_indent = dash_indent + _STEP
        end = index + 1
        while end < len(lines):
            following = lines[end]
            if following.strip() and _indent_of(following) <= dash_indent:
                break
            end += 1
        return index, end, key_indent

    msg = f"no column named {column!r} in this schema file"
    raise SchemaEditError(msg)


def _without_keys(block: list[str], keys: set[str], key_indent: int) -> list[str]:
    """Drop the named top-level keys of a column block, with their continuations."""
    kept: list[str] = []
    dropping = False
    for line in block:
        indent = _indent_of(line)
        if line.strip() and indent == key_indent:
            name = line.strip().split(":", 1)[0]
            dropping = name in keys
            if dropping:
                continue
        elif dropping and (not line.strip() or indent > key_indent):
            continue
        else:
            dropping = False
        kept.append(line)
    return kept


def set_column_description_and_meta(
    source: str, column: str, description: str, meta: dict[str, str]
) -> str:
    """Return `source` with one column's description and meta replaced.

    Everything outside that column's block — comments, other models, other
    columns, trailing whitespace — is returned unchanged.
    """
    lines = source.split("\n")
    start, end, key_indent = _column_span(lines, column)

    block = _without_keys(lines[start + 1 : end], {"description", "meta"}, key_indent)
    rebuilt = (
        lines[: start + 1]
        + _block_scalar(description, key_indent)
        + _mapping("meta", meta, key_indent)
        + block
        + lines[end:]
    )
    return "\n".join(rebuilt)


def read_column_description(source: str, model: str, column: str) -> str | None:
    """Parse `source` and return one column's description, for verification."""
    document = yaml.safe_load(source)
    for entry in (document or {}).get("models", []):
        if entry.get("name") != model:
            continue
        for item in entry.get("columns", []):
            if item.get("name") == column:
                value = item.get("description")
                return str(value) if value is not None else None
    return None


def read_column_meta(source: str, model: str, column: str) -> dict[str, str]:
    """Parse `source` and return one column's meta, for verification."""
    document = yaml.safe_load(source)
    for entry in (document or {}).get("models", []):
        if entry.get("name") != model:
            continue
        for item in entry.get("columns", []):
            if item.get("name") == column:
                return {str(k): str(v) for k, v in (item.get("meta") or {}).items()}
    return {}
