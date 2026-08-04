"""Locating a dbt project and resolving its models to DataHub dataset URNs.

`ci/diff/extract.py` needs two things it cannot get from a SQL file alone: the
warehouse identity of the relation a model builds, and the URN DataHub knows it
by. Both live in the dbt project — in `target/manifest.json` when the project
has been compiled, and in `dbt_project.yml` when it has not.

Resolution is ordered, and which path was taken is always visible:

1. **`target/manifest.json`** — authoritative. `database`, `schema` and `alias`
   are the values dbt itself resolved, and `metadata.adapter_type` is what
   DataHub's dbt source maps to a data platform. A URN built from these matches
   the URN the ingestion recipe emits.
2. **`dbt_project.yml`** — a fallback, on the `dbt` platform rather than the
   warehouse platform, because without a manifest the warehouse identity is
   genuinely unknown and inventing one would produce a URN that silently
   matches nothing in DataHub. The fallback is logged at WARNING with the
   command that would remove the need for it.

There is no third path. If neither file is found, resolution raises rather than
constructing something plausible from the filename.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final

from contracts.errors import BlastRadiusError

logger = logging.getLogger(__name__)

#: DataHub's fabric/environment segment. The demo and the fixtures are both PROD.
DEFAULT_ENV: Final[str] = "PROD"

#: Platform used when no compiled manifest is available. DataHub models dbt
#: itself as a platform, so this URN is a real address rather than a guess at
#: the warehouse one.
DBT_PLATFORM: Final[str] = "dbt"

#: How deep to look for a dbt project when the caller gives a project-relative
#: path. Bounded so a large monorepo does not turn a parse into a filesystem
#: walk.
_MAX_DISCOVERY_DEPTH: Final[int] = 4

_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")


class DbtProjectNotFoundError(BlastRadiusError):
    """No dbt project could be located for a path."""


class DatasetResolutionError(BlastRadiusError):
    """A model exists but its dataset identity cannot be determined."""


@dataclass(frozen=True, slots=True)
class DatasetRef:
    """The identity of the relation a dbt model builds, as DataHub knows it."""

    urn: str
    name: str
    platform: str
    #: True when the identity came from a compiled manifest rather than the
    #: dbt_project.yml fallback. Callers surface this; they must not silently
    #: treat the two as equivalent.
    from_manifest: bool


def _urn_for(platform: str, name: str, env: str = DEFAULT_ENV) -> str:
    return f"urn:li:dataset:(urn:li:dataPlatform:{platform},{name},{env})"


def _sanitise(part: str) -> str:
    """Return `part` if it is a bare identifier, else raise.

    A dataset name segment reaches a URN, and a URN with a stray character in it
    fails to match in DataHub in a way that looks like "no lineage" rather than
    like an error. Refusing early is cheaper than debugging that.
    """
    if not _IDENTIFIER.match(part):
        msg = f"{part!r} is not a usable dataset name segment"
        raise DatasetResolutionError(msg)
    return part


@dataclass(frozen=True, slots=True)
class DbtProject:
    """A dbt project on disk, and the manifest it has or has not been compiled to."""

    root: Path
    project_name: str
    manifest_path: Path | None

    # ------------------------------------------------------------------
    # discovery
    # ------------------------------------------------------------------

    @classmethod
    def at(cls, root: Path, manifest_path: Path | None = None) -> DbtProject:
        """Load the project rooted at `root`."""
        project_file = root / "dbt_project.yml"
        if not project_file.is_file():
            msg = f"no dbt_project.yml in {root}"
            raise DbtProjectNotFoundError(msg)
        name = _read_project_name(project_file)
        manifest = manifest_path or (root / "target" / "manifest.json")
        return cls(
            root=root,
            project_name=name,
            manifest_path=manifest if manifest.is_file() else None,
        )

    @classmethod
    def discover(cls, model_path: str | Path, start: Path | None = None) -> DbtProject:
        """Find the dbt project that owns `model_path`.

        `model_path` is project-relative — it is what appears in a dbt diff and
        in `ColumnChange.file_path` — so the project root is the directory that
        both contains a `dbt_project.yml` and resolves that path to a real file.

        Looks first for an ancestor of `model_path` (the normal case: the tool
        runs from the repository root and dbt lives at the root), then for a
        project nested under `start`, deepest paths last so the result does not
        depend on directory iteration order.
        """
        model_path = Path(model_path)
        base = (start or Path.cwd()).resolve()

        if model_path.is_absolute():
            for parent in model_path.parents:
                if (parent / "dbt_project.yml").is_file():
                    return cls.at(parent)
            msg = f"no dbt_project.yml above {model_path}"
            raise DbtProjectNotFoundError(msg)

        if (base / "dbt_project.yml").is_file() and (base / model_path).is_file():
            return cls.at(base)

        for candidate in _nested_project_roots(base):
            if (candidate / model_path).is_file():
                return cls.at(candidate)

        msg = (
            f"no dbt project under {base} resolves {model_path}. "
            "Pass --project-dir, or run from the repository root."
        )
        raise DbtProjectNotFoundError(msg)

    # ------------------------------------------------------------------
    # resolution
    # ------------------------------------------------------------------

    def dataset_for(self, model_name: str, model_path: str | Path) -> DatasetRef:
        """Return the dataset identity of `model_name`.

        Manifest first; `dbt_project.yml` second, on the dbt platform, with a
        warning naming the command that would make the first path available.
        """
        if self.manifest_path is not None:
            resolved = _dataset_from_manifest(self.manifest_path, model_name)
            if resolved is not None:
                return resolved
            logger.warning(
                "model %r is not in %s; falling back to a dbt-platform URN. "
                "The manifest is stale — re-run `dbt compile`.",
                model_name,
                self.manifest_path,
            )
        else:
            logger.warning(
                "no compiled manifest under %s; falling back to a dbt-platform URN for %r. "
                "Run `dbt compile` for warehouse-accurate URNs.",
                self.root / "target",
                model_name,
            )

        schema = _schema_from_path(model_path)
        segments = (self.project_name, schema, model_name)
        name = ".".join(_sanitise(part) for part in segments if part)
        return DatasetRef(
            urn=_urn_for(DBT_PLATFORM, name),
            name=name,
            platform=DBT_PLATFORM,
            from_manifest=False,
        )


def _nested_project_roots(base: Path) -> list[Path]:
    """Return dbt project roots under `base`, shallowest first, deterministically."""
    roots: list[Path] = []
    for depth in range(1, _MAX_DISCOVERY_DEPTH + 1):
        pattern = "/".join(["*"] * depth) + "/dbt_project.yml"
        roots.extend(sorted(p.parent for p in base.glob(pattern)))
    return roots


def _read_project_name(project_file: Path) -> str:
    """Read `name:` from dbt_project.yml.

    Deliberately a line scan rather than a YAML parse: this is one scalar at the
    top level of a file dbt itself constrains, and reading it this way keeps
    project discovery free of a parser dependency.
    """
    for line in project_file.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^name\s*:\s*[\"']?([A-Za-z0-9_]+)[\"']?\s*(?:#.*)?$", line)
        if match:
            return match.group(1)
    msg = f"{project_file} has no top-level `name:`"
    raise DatasetResolutionError(msg)


def _schema_from_path(model_path: str | Path) -> str:
    """Return the model's directory under `models/`, which dbt uses as its schema.

    `models/staging/stg_customers.sql` → `staging`. A model directly under
    `models/` has no custom schema; the caller drops the empty segment.
    """
    parts = Path(model_path).parent.parts
    if not parts:
        return ""
    tail = parts[-1]
    return "" if tail in {"models", "."} else tail


@lru_cache(maxsize=8)
def _load_manifest(manifest_path: Path) -> dict[str, object]:
    with manifest_path.open(encoding="utf-8") as handle:
        payload: dict[str, object] = json.load(handle)
    return payload


def _dataset_from_manifest(manifest_path: Path, model_name: str) -> DatasetRef | None:
    """Resolve a model through a compiled manifest, or return None if absent.

    The URN is assembled the way DataHub's dbt source assembles it: the adapter
    type is the platform, and `database.schema.alias` is the dataset name. Both
    come from the manifest, so the result matches what ingestion emitted rather
    than approximating it.
    """
    manifest = _load_manifest(manifest_path)
    nodes = manifest.get("nodes")
    if not isinstance(nodes, dict):
        return None

    node = next(
        (
            value
            for value in nodes.values()
            if isinstance(value, dict)
            and value.get("resource_type") == "model"
            and value.get("name") == model_name
        ),
        None,
    )
    if node is None:
        return None

    metadata = manifest.get("metadata")
    adapter = metadata.get("adapter_type") if isinstance(metadata, dict) else None
    database = node.get("database")
    schema = node.get("schema")
    relation = node.get("alias") or node.get("name")
    if not (adapter and database and schema and relation):
        return None

    name = ".".join(_sanitise(str(part)) for part in (database, schema, relation))
    return DatasetRef(
        urn=_urn_for(_sanitise(str(adapter)), name),
        name=name,
        platform=str(adapter),
        from_manifest=True,
    )
