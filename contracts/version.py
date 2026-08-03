"""Single source of the version string.

Lives in `contracts/` because both halves stamp it into artifacts they produce
and OWNER B cannot read `core/`. `contracts/tests/test_repo_invariants.py`
fails if it drifts from `project.version` in pyproject.toml.
"""

from __future__ import annotations

from typing import Final

VERSION: Final[str] = "0.1.0"
