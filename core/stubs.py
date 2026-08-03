"""Find every unimplemented stub in the repository, and say who owns it.

Static, not dynamic: the inventory is built by parsing the source with `ast`,
so it never goes stale, never needs a decorator to be remembered, and works
across both owners' code without importing anything. Importing `ci/` from `core/`
to enumerate its stubs would violate the isolation model this repo is built on.

Ownership is read from `.github/CODEOWNERS` rather than duplicated here, so the
work split shown by `blast-radius stubs` is the same one GitHub enforces on a
pull request. If they ever disagree, CODEOWNERS is wrong or this is, and
`core/tests/test_stub_inventory.py` will say which.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final

#: Directories that never contain project source.
EXCLUDED_DIRS: Final[frozenset[str]] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "build",
        "dist",
        "target",
        "dbt_packages",
        "out",
    }
)

#: Maps a CODEOWNERS handle to the label used in reports.
OWNER_LABELS: Final[dict[str, str]] = {
    "@etka": "A (etka)",
    "@teammate": "B (teammate)",
}

UNOWNED_LABEL: Final[str] = "unassigned"


@dataclass(frozen=True, slots=True)
class StubRef:
    """One function that raises NotImplementedError."""

    file: str
    line: int
    qualname: str
    module: str
    contract: str
    handles: tuple[str, ...]
    owner_label: str

    @property
    def location(self) -> str:
        """`path:line` for a clickable terminal reference."""
        return f"{self.file}:{self.line}"


@lru_cache(maxsize=1)
def repo_root() -> Path:
    """Return the repository root, walking up from this file."""
    here = Path(__file__).resolve()
    for candidate in (here.parent, *here.parents):
        if (candidate / "contracts").is_dir() and (candidate / "pyproject.toml").is_file():
            return candidate
    return here.parent.parent


def parse_codeowners(path: Path) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Parse CODEOWNERS into (pattern, handles) pairs, in file order.

    Deliberately supports only the subset this repository uses: leading-slash
    directory patterns and `*`. A fuller implementation would be a different
    project.
    """
    if not path.is_file():
        return ()
    rules: list[tuple[str, tuple[str, ...]]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        rules.append((parts[0], tuple(parts[1:])))
    return tuple(rules)


def _pattern_matches(pattern: str, rel_path: str) -> bool:
    if pattern == "*":
        return True
    normalized = pattern.lstrip("/").rstrip("/")
    if not normalized:
        return True
    return rel_path == normalized or rel_path.startswith(f"{normalized}/")


def owners_for(rel_path: str, rules: tuple[tuple[str, tuple[str, ...]], ...]) -> tuple[str, ...]:
    """Return the CODEOWNERS handles for a repo-relative path. Last match wins."""
    handles: tuple[str, ...] = ()
    for pattern, owners in rules:
        if _pattern_matches(pattern, rel_path):
            handles = owners
    return handles


def label_for(handles: tuple[str, ...]) -> str:
    """Turn CODEOWNERS handles into a display label."""
    if not handles:
        return UNOWNED_LABEL
    labels = [OWNER_LABELS.get(h, h) for h in handles]
    if len(labels) > 1:
        return "shared (" + " + ".join(labels) + ")"
    return labels[0]


def _raised_exception_name(node: ast.Raise) -> str | None:
    exc = node.exc
    if exc is None:
        return None
    if isinstance(exc, ast.Call):
        exc = exc.func
    if isinstance(exc, ast.Name):
        return exc.id
    if isinstance(exc, ast.Attribute):
        return exc.attr
    return None


def _raises_not_implemented(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True when the function body raises NotImplementedError at its top level.

    Top level only, on purpose: a `raise NotImplementedError` inside an `if`
    branch is a real code path, not a scaffold, and counting it would make the
    inventory lie about how much work is left.
    """
    return any(
        isinstance(stmt, ast.Raise)
        and (name := _raised_exception_name(stmt)) is not None
        and name.endswith("NotImplementedError")
        for stmt in node.body
    )


def _summary_of(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    doc = ast.get_docstring(node, clean=True)
    if not doc:
        return "(no docstring — every stub must document its contract)"
    return doc.strip().splitlines()[0]


class _StubVisitor(ast.NodeVisitor):
    """Collect stubs with their qualified names."""

    def __init__(self) -> None:
        self.stack: list[str] = []
        self.found: list[tuple[str, int, str]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if _raises_not_implemented(node):
            qualname = ".".join([*self.stack, node.name])
            self.found.append((qualname, node.lineno, _summary_of(node)))
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)


def _module_name(rel_path: Path) -> str:
    parts = list(rel_path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def iter_source_files(root: Path) -> list[Path]:
    """Return every project Python file under `root`, in stable order."""
    files: list[Path] = []
    for path in sorted(root.rglob("*.py")):
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        files.append(path)
    return files


def find_stubs(root: Path | None = None) -> tuple[StubRef, ...]:
    """Return every stub in the repository, sorted by file then line."""
    base = root or repo_root()
    rules = parse_codeowners(base / ".github" / "CODEOWNERS")

    stubs: list[StubRef] = []
    for path in iter_source_files(base):
        rel = path.relative_to(base)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        visitor = _StubVisitor()
        visitor.visit(tree)
        if not visitor.found:
            continue
        handles = owners_for(rel.as_posix(), rules)
        for qualname, lineno, summary in visitor.found:
            stubs.append(
                StubRef(
                    file=rel.as_posix(),
                    line=lineno,
                    qualname=qualname,
                    module=_module_name(rel),
                    contract=summary,
                    handles=handles,
                    owner_label=label_for(handles),
                )
            )
    return tuple(sorted(stubs, key=lambda s: (s.file, s.line)))


def group_by_owner(stubs: tuple[StubRef, ...]) -> dict[str, list[StubRef]]:
    """Group stubs by owner label, owners in alphabetical order."""
    grouped: dict[str, list[StubRef]] = {}
    for stub in stubs:
        grouped.setdefault(stub.owner_label, []).append(stub)
    return {key: grouped[key] for key in sorted(grouped)}
