"""The model must not be able to reach the severity engine. Proved twice.

Constraint 3 of the project: severity scoring, lineage traversal and diff
parsing are deterministic; the LLM writes prose and candidate code and nothing
else. A comment saying so is worth very little — the next person to add a
"just ask the model to sanity check the score" line would not read it.

So this is checked structurally, in two independent ways:

1. **Statically**, by parsing every module under `core/severity/` and
   `core/impact/` and looking at what it imports. Catches a direct import.
2. **Dynamically**, by importing `core.severity` in a clean subprocess and
   asserting that neither `core.agent` nor `anthropic` ended up in
   `sys.modules`. Catches a transitive import through a third module, which
   static analysis of one directory would miss.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Packages that must never be reachable from the deterministic core.
FORBIDDEN_PREFIXES = ("core.agent", "anthropic")

#: Directories whose determinism is load-bearing.
DETERMINISTIC_PACKAGES = ("core/severity", "core/impact")


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def _python_files(relative_dir: str) -> list[Path]:
    return sorted((REPO_ROOT / relative_dir).rglob("*.py"))


@pytest.mark.parametrize("package", DETERMINISTIC_PACKAGES)
def test_deterministic_packages_do_not_import_the_agent(package: str) -> None:
    offenders: list[str] = []
    for path in _python_files(package):
        for module in _imported_modules(path):
            if module.startswith(FORBIDDEN_PREFIXES):
                offenders.append(f"{path.relative_to(REPO_ROOT)} imports {module}")
    assert not offenders, "the deterministic core must not import the LLM layer:\n  " + "\n  ".join(
        offenders
    )


def test_severity_does_not_pull_in_the_agent_transitively() -> None:
    """Import core.severity in a clean interpreter and check what came with it."""
    probe = (
        "import sys; import core.severity; "
        "leaked = sorted(m for m in sys.modules "
        "if m.startswith('core.agent') or m.startswith('anthropic')); "
        "print(','.join(leaked))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    leaked = result.stdout.strip()
    assert leaked == "", f"importing core.severity pulled in: {leaked}"


def test_severity_input_has_no_field_that_can_carry_prose() -> None:
    """The engine's input type must have nowhere for text to arrive.

    This is the strongest of the three checks. The other two say the model is
    not reached today; this one says there is no parameter through which a
    description, a PR body or a model opinion could ever be passed, whatever a
    future caller decides to do.
    """
    from core.severity.rules import SeverityInput

    allowed = {
        "change_kind": str,  # a closed enum, not free text
        "downstream_count": int,
        "nearest_hop_distance": (int, type(None)),
        "query_count": (int, type(None)),
        "has_data_contract": bool,
        "has_assertion": bool,
        "has_critical_consumer": bool,
    }
    actual = set(SeverityInput.__dataclass_fields__)
    assert actual == set(allowed), (
        "SeverityInput gained or lost a field. Every field must be a graph fact; "
        f"got {sorted(actual)}"
    )

    from typing import get_args

    from contracts.models import ChangeKind

    assert set(get_args(ChangeKind)) == {"removed", "renamed", "type_changed", "added"}, (
        "change_kind must stay a closed enum; a free-form string would be a text channel"
    )


def test_agent_module_cannot_construct_a_severity() -> None:
    """`core.agent` must not import the severity engine either.

    The boundary is symmetric on purpose. If the agent could import `compute`,
    a future refactor could have it 'suggest' a score, and the resulting object
    would carry `computed_by: "deterministic"` while being nothing of the sort.
    """
    offenders: list[str] = []
    for path in _python_files("core/agent"):
        for module in _imported_modules(path):
            if module.startswith("core.severity"):
                offenders.append(f"{path.relative_to(REPO_ROOT)} imports {module}")
    assert not offenders, "the agent must not reach the severity engine:\n  " + "\n  ".join(
        offenders
    )
