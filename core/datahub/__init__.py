"""DataHub access. Two paths, one interface."""

from core.datahub.base import (
    DEFAULT_MAX_HOPS,
    DEFAULT_USAGE_WINDOW_DAYS,
    DataHubReader,
    EntityRef,
    LineageDirection,
    LineagePath,
    SchemaFieldInfo,
    contract_covers_column,
)
from core.datahub.factory import build_reader
from core.datahub.mcp_client import McpDataHubReader
from core.datahub.sdk_client import SdkDataHubReader

__all__ = [
    "DEFAULT_MAX_HOPS",
    "DEFAULT_USAGE_WINDOW_DAYS",
    "DataHubReader",
    "EntityRef",
    "LineageDirection",
    "LineagePath",
    "McpDataHubReader",
    "SchemaFieldInfo",
    "SdkDataHubReader",
    "build_reader",
    "contract_covers_column",
]
