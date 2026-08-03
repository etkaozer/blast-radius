"""Runtime configuration, read once from the environment.

Real implementation, not a stub: `blast-radius doctor` has to be able to tell a
developer exactly what is missing before they spend an afternoon debugging a
connection, and it cannot do that if reading configuration is itself unfinished.

Secrets are held as strings and never logged. `redacted()` is the only
representation that may be printed.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Literal

from core.datahub.base import DEFAULT_MAX_HOPS, DEFAULT_USAGE_WINDOW_DAYS
from core.errors import ConfigurationError

DataHubMode = Literal["mcp", "sdk"]

DEFAULT_GMS_URL = "http://localhost:8080"
DEFAULT_MCP_SERVER_COMMAND = "mcp-server-datahub"
DEFAULT_MODEL = "claude-sonnet-5"

#: Mutation tools are only exposed by mcp-server-datahub from this version on,
#: and only when the server was started with TOOLS_IS_MUTATION_ENABLED=true.
MIN_MCP_SERVER_VERSION = "0.5.0"


def _flag(raw: str | None, *, default: bool = False) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(raw: str | None, default: int) -> int:
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        msg = f"expected an integer, got {raw!r}"
        raise ConfigurationError(msg) from exc


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything blast-radius needs from its environment."""

    datahub_gms_url: str = DEFAULT_GMS_URL
    datahub_token: str | None = None
    datahub_mode: DataHubMode = "mcp"
    mcp_server_command: str = DEFAULT_MCP_SERVER_COMMAND
    mutation_enabled: bool = False
    anthropic_api_key: str | None = None
    model: str = DEFAULT_MODEL
    github_token: str | None = None
    max_hops: int = DEFAULT_MAX_HOPS
    usage_window_days: int = DEFAULT_USAGE_WINDOW_DAYS
    dbt_project_dir: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Build settings from the process environment (or an explicit mapping)."""
        source = os.environ if env is None else env
        mode_raw = (source.get("BLAST_RADIUS_DATAHUB_MODE") or "mcp").strip().lower()
        if mode_raw not in ("mcp", "sdk"):
            msg = f"BLAST_RADIUS_DATAHUB_MODE must be 'mcp' or 'sdk', got {mode_raw!r}"
            raise ConfigurationError(msg)
        mode: DataHubMode = "mcp" if mode_raw == "mcp" else "sdk"

        return cls(
            datahub_gms_url=source.get("DATAHUB_GMS_URL") or DEFAULT_GMS_URL,
            datahub_token=source.get("DATAHUB_GMS_TOKEN") or None,
            datahub_mode=mode,
            mcp_server_command=source.get("BLAST_RADIUS_MCP_COMMAND") or DEFAULT_MCP_SERVER_COMMAND,
            mutation_enabled=_flag(source.get("TOOLS_IS_MUTATION_ENABLED")),
            anthropic_api_key=source.get("ANTHROPIC_API_KEY") or None,
            model=source.get("BLAST_RADIUS_MODEL") or DEFAULT_MODEL,
            github_token=source.get("GITHUB_TOKEN") or None,
            max_hops=_int(source.get("BLAST_RADIUS_MAX_HOPS"), DEFAULT_MAX_HOPS),
            usage_window_days=_int(
                source.get("BLAST_RADIUS_USAGE_WINDOW_DAYS"), DEFAULT_USAGE_WINDOW_DAYS
            ),
            dbt_project_dir=source.get("BLAST_RADIUS_DBT_PROJECT_DIR") or None,
        )

    def require_datahub(self) -> None:
        """Raise if the DataHub read path cannot possibly work."""
        if not self.datahub_gms_url:
            msg = "DATAHUB_GMS_URL is not set"
            raise ConfigurationError(msg)

    def require_agent(self) -> None:
        """Raise if the LLM path is requested but unusable."""
        if not self.anthropic_api_key:
            msg = (
                "ANTHROPIC_API_KEY is not set. Run with --no-agent to produce a report "
                "without prose or generated fixes; severity and lineage do not need it."
            )
            raise ConfigurationError(msg)

    def redacted(self) -> dict[str, str]:
        """Return a printable view with every secret replaced by its presence."""

        def mask(value: str | None) -> str:
            return "set" if value else "not set"

        return {
            "datahub_gms_url": self.datahub_gms_url,
            "datahub_token": mask(self.datahub_token),
            "datahub_mode": self.datahub_mode,
            "mcp_server_command": self.mcp_server_command,
            "mutation_enabled": str(self.mutation_enabled).lower(),
            "anthropic_api_key": mask(self.anthropic_api_key),
            "model": self.model,
            "github_token": mask(self.github_token),
            "max_hops": str(self.max_hops),
            "usage_window_days": str(self.usage_window_days),
            "dbt_project_dir": self.dbt_project_dir or "not set",
        }

    def with_overrides(self, **kwargs: object) -> Settings:
        """Return a copy with CLI flags applied over the environment."""
        return replace(self, **kwargs)  # type: ignore[arg-type]
