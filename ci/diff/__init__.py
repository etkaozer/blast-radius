"""Deterministic diff extraction. No LLM, by contract."""

from ci.diff.extract import (
    FileDiff,
    build_change_set,
    collect_untrusted_text,
    diff_columns,
    parse_projection,
)
from ci.diff.git import ChangedFile, collect_file_diffs

__all__ = [
    "ChangedFile",
    "FileDiff",
    "build_change_set",
    "collect_file_diffs",
    "collect_untrusted_text",
    "diff_columns",
    "parse_projection",
]
