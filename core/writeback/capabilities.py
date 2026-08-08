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

import re
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from importlib.util import find_spec
from typing import Final

_T = "core.writeback.capabilities"

MIN_MCP_SERVER_VERSION: Final[str] = "0.5.0"
MUTATION_ENV_VAR: Final[str] = "TOOLS_IS_MUTATION_ENABLED"

#: Probes are diagnostics, not work. If one of them is slow the answer is "we
#: could not tell", not a command that appears to hang.
PROBE_TIMEOUT_SECONDS: Final[int] = 10

_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def version_tuple(raw: str | None) -> tuple[int, int, int] | None:
    """Parse the first dotted version in a string, or None.

    Needed because the comparison has to be numeric: "0.10.0" is newer than
    "0.5.0" and string ordering says the opposite, which would silently disable
    the write path on an up-to-date server.
    """
    if not raw:
        return None
    match = _VERSION.search(raw)
    if match is None:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def meets_minimum(found: str | None, minimum: str = MIN_MCP_SERVER_VERSION) -> bool:
    """Return True when `found` is at least `minimum`, comparing numerically."""
    parsed = version_tuple(found)
    floor = version_tuple(minimum)
    if parsed is None or floor is None:
        return False
    return parsed >= floor


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


def probe_mcp_version(mcp_server_command: str) -> tuple[str | None, str | None]:
    """Return (version, note) for the installed MCP server. Never raises.

    Read-only by construction: `--version` cannot mutate a catalog.
    """
    executable = shutil.which(mcp_server_command)
    if executable is None:
        return None, f"{mcp_server_command!r} is not on PATH"

    try:
        completed = subprocess.run(  # fixed argv, never a shell string
            [executable, "--version"],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"could not run {mcp_server_command} --version: {exc}"

    reported = f"{completed.stdout or ''} {completed.stderr or ''}".strip()
    parsed = version_tuple(reported)
    if parsed is None:
        return None, f"{mcp_server_command} did not report a parseable version"
    return ".".join(str(part) for part in parsed), None


def probe_gms(gms_url: str) -> tuple[bool, str | None]:
    """Return (reachable, note) for the GMS health endpoint. Never raises."""
    if not gms_url:
        return False, "DATAHUB_GMS_URL is empty"
    if not gms_url.startswith(("http://", "https://")):
        return False, f"DATAHUB_GMS_URL {gms_url!r} is not an http(s) URL"

    url = f"{gms_url.rstrip('/')}/health"
    request = urllib.request.Request(url, method="GET")  # scheme checked above
    try:
        with urllib.request.urlopen(request, timeout=PROBE_TIMEOUT_SECONDS) as response:
            return 200 <= response.status < 300, None
    except urllib.error.HTTPError as exc:
        return False, f"GMS health check returned {exc.code}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, f"GMS at {gms_url} is unreachable: {exc}"


def detect(
    gms_url: str,
    mcp_server_command: str,
    mutation_env_flag: bool,
    token: str | None = None,
) -> WriteCapabilities:
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
    - `sdk_available` means acryl-datahub imports AND GMS is reachable. A token
      is NOT required: an open-source DataHub has metadata service
      authentication disabled by default and issues none.
    - `gms_reachable` is a cheap health check against `gms_url`.
    - Never raise. Every failure becomes a False and a note; the whole point of
      this function is to report a broken environment rather than to die in one.
    """
    notes: list[str] = []

    mcp_version, version_note = probe_mcp_version(mcp_server_command)
    if version_note:
        notes.append(version_note)

    mcp_available = meets_minimum(mcp_version)
    if mcp_version is not None and not mcp_available:
        notes.append(
            f"mcp-server-datahub {mcp_version} is older than the v{MIN_MCP_SERVER_VERSION} "
            "that first exposed mutation tools"
        )

    if mcp_available and not mutation_env_flag:
        notes.append(f"{MUTATION_ENV_VAR} is not true, so the MCP mutation tools are hidden")

    gms_reachable, gms_note = probe_gms(gms_url)
    if gms_note:
        notes.append(gms_note)

    sdk_importable = find_spec("datahub") is not None
    if not sdk_importable:
        notes.append("acryl-datahub is not installed; run `uv sync --extra datahub`")
    elif token is None:
        # Not fatal, and treating it as fatal was a real bug: an open-source
        # `datahub docker quickstart` ships with metadata service authentication
        # disabled and issues no token at all, so requiring one reported "no
        # write path available" on precisely the deployment the demo runs on.
        # A secured deployment rejects the write instead, which surfaces as a
        # failed write rather than as a missing capability — the honest place
        # for it, because that is what actually happened.
        notes.append(
            "DATAHUB_GMS_TOKEN is not set; the SDK write path will make unauthenticated "
            "requests. An open-source quickstart accepts these; a deployment with "
            "metadata service authentication enabled will reject them."
        )

    # Proposals are a DataHub Cloud feature advertised in the MCP server's tool
    # list. Establishing an MCP session is more than a probe should do, and the
    # URL is explicitly not evidence, so this stays False and says why rather
    # than guessing from the hostname.
    notes.append(
        "proposal support was not probed: it requires an MCP session, and inferring it "
        "from the URL would be a guess"
    )

    return WriteCapabilities(
        mcp_available=mcp_available,
        mcp_version=mcp_version,
        mcp_mutations_enabled=mcp_available and mutation_env_flag,
        proposals_available=False,
        sdk_available=sdk_importable and gms_reachable,
        gms_reachable=gms_reachable,
        notes=tuple(notes),
    )
