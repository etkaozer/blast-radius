"""DataHub write-back: the finding becomes metadata the next agent inherits."""

from core.writeback.capabilities import (
    MIN_MCP_SERVER_VERSION,
    MUTATION_ENV_VAR,
    WriteCapabilities,
    detect,
)
from core.writeback.doctor import CheckResult, exit_code_for, run_checks
from core.writeback.record import build_record
from core.writeback.writer import (
    STRUCTURED_PROPERTY_URN,
    DataHubWriter,
    McpDataHubWriter,
    SdkDataHubWriter,
    build_writer,
    tag_urn_for,
)

__all__ = [
    "MIN_MCP_SERVER_VERSION",
    "MUTATION_ENV_VAR",
    "STRUCTURED_PROPERTY_URN",
    "CheckResult",
    "DataHubWriter",
    "McpDataHubWriter",
    "SdkDataHubWriter",
    "WriteCapabilities",
    "build_record",
    "build_writer",
    "detect",
    "exit_code_for",
    "run_checks",
    "tag_urn_for",
]
