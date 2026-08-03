"""What the configured DataHub deployment will actually let us write.

Writing back to DataHub is where the demo becomes a product: the finding stops
being a comment on a pull request and becomes metadata the next human or agent
inherits. It is also the part most likely to be unavailable on a judge's laptop,
so it is designed to degrade in a way that is visible rather than silent.

The constraints, stated once here so nothing else has to guess:

* Mutation tools are exposed by `mcp-server-datahub` only from v0.5.0, and only
  when the server was started with `TOOLS_IS_MUTATION_ENABLED=true`.
* Proposal tools (propose rather than apply) are a DataHub Cloud feature and are
  absent from an open-source quickstart.
* The Python SDK can emit the same aspects without either, which is why it is
  the fallback rather than a second-class path.

`blast-radius doctor` exists so that all of this is discovered before anyone
depends on it, and never at the end of a demo.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from core.errors import OWNER_A, StubNotImplementedError

_T = "core.writeback.capabilities"

MIN_MCP_SERVER_VERSION: Final[str] = "0.5.0"
MUTATION_ENV_VAR: Final[str] = "TOOLS_IS_MUTATION_ENABLED"


@dataclass(frozen=True, slots=True)
class WriteCapabilities:
    """The write surface available in this environment."""

    mcp_available: bool
    mcp_version: str | None
    mcp_mutations_enabled: bool
    proposals_available: bool
    sdk_available: bool
    gms_reachable: bool
    notes: tuple[str, ...] = ()

    @property
    def can_write(self) -> bool:
        """True when at least one write path is usable."""
        return (self.mcp_available and self.mcp_mutations_enabled) or self.sdk_available

    @property
    def preferred_path(self) -> str:
        """Which path a write would take right now: 'mcp', 'sdk' or 'none'."""
        if self.mcp_available and self.mcp_mutations_enabled:
            return "mcp"
        if self.sdk_available:
            return "sdk"
        return "none"

    def explain(self) -> str:
        """One human-readable line for `doctor` and for the report's degradations."""
        if self.preferred_path == "mcp":
            return f"MCP mutations enabled (server {self.mcp_version or 'unknown'})."
        if self.preferred_path == "sdk":
            reason = (
                f"mcp-server-datahub not found or older than v{MIN_MCP_SERVER_VERSION}"
                if not self.mcp_available
                else f"{MUTATION_ENV_VAR} is not true"
            )
            return f"Falling back to the Python SDK write path: {reason}."
        return (
            "No write path available: MCP mutations are disabled and the Python SDK "
            "is not installed or GMS is unreachable."
        )


def detect(gms_url: str, mcp_server_command: str, mutation_env_flag: bool) -> WriteCapabilities:
    """Probe the environment for a usable write path.

    Contract:

    - Read only. This function must never create, tag or modify an entity to
      find out whether it could; probing by writing is how a doctor command
      pollutes a production catalog.
    - Determine `mcp_version` by invoking the server command with `--version`
      (or the MCP initialize handshake) and compare against
      `MIN_MCP_SERVER_VERSION` using a real version comparison, not string
      ordering: "0.10.0" is newer than "0.5.0".
    - `proposals_available` is true only on DataHub Cloud. Detect it from the
      server's advertised tool list, never from the URL.
    - `sdk_available` means acryl-datahub imports AND a token is present.
    - `gms_reachable` is a cheap health check against `gms_url`.
    - Never raise. Every failure becomes a False and a note; the whole point of
      this function is to report a broken environment rather than to die in one.
    """
    raise StubNotImplementedError(
        f"{_T}.detect",
        OWNER_A,
        "read-only probe of MCP version, mutation flag, proposal availability, SDK and GMS; "
        "returns WriteCapabilities and never raises",
    )
