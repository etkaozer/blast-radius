"""Deterministic diff extraction. No LLM, by contract."""

from ci.diff.extract import (
    FileDiff,
    build_change_set,
    collect_untrusted_text,
    diff_columns,
    parse_projection,
)

__all__ = [
    "FileDiff",
    "build_change_set",
    "collect_untrusted_text",
    "diff_columns",
    "parse_projection",
]
