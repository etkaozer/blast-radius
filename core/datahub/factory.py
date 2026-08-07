"""Choose an access path. Real implementation: the choice is configuration, not analysis."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.datahub.base import DataHubReader
from core.datahub.hybrid import HybridDataHubReader
from core.datahub.mcp_client import McpDataHubReader
from core.datahub.sdk_client import SdkDataHubReader

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance only
    from core.config import Settings


def build_reader(settings: Settings) -> DataHubReader:
    """Return the reader for the configured access path.

    Every branch returns an object satisfying `DataHubReader` structurally; the
    caller never learns which one it got except through `reader.access_path`,
    which is recorded in the report.

    `datahub_mode="mcp"` builds the COMPOSED reader rather than a bare
    `McpDataHubReader`, and reports itself as `"mcp+sdk"`. The reason is in
    `core/datahub/hybrid.py`: `mcp-server-datahub` cannot read data contracts at
    all, and cannot read assertions on an open-source DataHub, so a bare MCP
    reader would produce a report missing two of the seven severity factors. The
    mode name stays "mcp" because it still selects MCP as the path for every
    read MCP can serve, lineage included.
    """
    settings.require_datahub()
    if settings.datahub_mode == "mcp":
        return HybridDataHubReader(
            mcp=McpDataHubReader(
                server_command=settings.mcp_server_command,
                gms_url=settings.datahub_gms_url,
                token=settings.datahub_token,
            ),
            sdk=SdkDataHubReader(gms_url=settings.datahub_gms_url, token=settings.datahub_token),
        )
    return SdkDataHubReader(gms_url=settings.datahub_gms_url, token=settings.datahub_token)
