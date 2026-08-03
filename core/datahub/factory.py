"""Choose an access path. Real implementation: the choice is configuration, not analysis."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.datahub.base import DataHubReader
from core.datahub.mcp_client import McpDataHubReader
from core.datahub.sdk_client import SdkDataHubReader

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance only
    from core.config import Settings


def build_reader(settings: Settings) -> DataHubReader:
    """Return the reader for the configured access path.

    Both branches return objects that satisfy `DataHubReader` structurally; the
    caller never learns which one it got except through
    `reader.access_path`, which is recorded in the report.
    """
    settings.require_datahub()
    if settings.datahub_mode == "mcp":
        return McpDataHubReader(
            server_command=settings.mcp_server_command,
            gms_url=settings.datahub_gms_url,
            token=settings.datahub_token,
        )
    return SdkDataHubReader(gms_url=settings.datahub_gms_url, token=settings.datahub_token)
