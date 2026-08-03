"""`blast-radius doctor` — verify the write path before anyone depends on it.

The failure this command exists to prevent: a team wires blast-radius into CI,
watches it post good PR comments for a week, and discovers on the day someone
needs the history that nothing was ever written back, because the MCP server was
started without mutations enabled and the failure was swallowed.

So: the checks that can run without DataHub run for real, right now, and the
ones that need DataHub report honestly that they are not implemented yet. A
doctor that lies about being healthy is worse than no doctor.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from typing import Literal

from contracts.loader import SCHEMA_FILES, iter_fixture_dirs, load_change_set, load_schema
from core.config import Settings
from core.errors import OWNER_A, StubNotImplementedError
from core.writeback.capabilities import MIN_MCP_SERVER_VERSION, MUTATION_ENV_VAR, detect

CheckStatus = Literal["ok", "warn", "fail", "not_implemented"]

_T = "core.writeback.doctor"


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One diagnostic line."""

    name: str
    status: CheckStatus
    detail: str
    owner: str | None = None

    @property
    def symbol(self) -> str:
        """A single character for terminal output."""
        return {"ok": "✓", "warn": "!", "fail": "✗", "not_implemented": "·"}[self.status]


#: The project's floor, as a named constant rather than a literal in the
#: comparison below — `doctor` is often the first thing someone runs on a
#: machine with the wrong interpreter, so the check has to survive being
#: analysed by tools that assume it is already satisfied.
MIN_PYTHON: tuple[int, int] = (3, 11)


def check_python() -> CheckResult:
    """Verify the interpreter meets the project's floor."""
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    floor = ".".join(str(part) for part in MIN_PYTHON)
    if sys.version_info[:2] >= MIN_PYTHON:
        return CheckResult("python", "ok", f"Python {version}")
    return CheckResult("python", "fail", f"Python {version}; {floor}+ required")


def check_contracts() -> CheckResult:
    """Verify every JSON Schema parses."""
    try:
        for name in SCHEMA_FILES:
            load_schema(name)
    except Exception as exc:
        return CheckResult("contracts", "fail", f"schema failed to load: {exc}")
    return CheckResult("contracts", "ok", f"{len(SCHEMA_FILES)} schemas loaded")


def check_fixtures() -> CheckResult:
    """Verify every golden fixture still satisfies its contract."""
    checked = 0
    for directory in iter_fixture_dirs():
        for path in sorted(directory.glob("change_set*.json")):
            try:
                load_change_set(path)
            except Exception as exc:
                return CheckResult("fixtures", "fail", f"{path.name}: {exc}")
            checked += 1
    return CheckResult("fixtures", "ok", f"{checked} change sets validate against the schema")


def check_environment(settings: Settings) -> list[CheckResult]:
    """Verify the configuration a run will need."""
    results = [
        CheckResult("datahub url", "ok", settings.datahub_gms_url)
        if settings.datahub_gms_url
        else CheckResult("datahub url", "fail", "DATAHUB_GMS_URL is not set"),
        CheckResult("datahub token", "ok", "set")
        if settings.datahub_token
        else CheckResult(
            "datahub token", "warn", "DATAHUB_GMS_TOKEN is not set; only anonymous reads will work"
        ),
        CheckResult("anthropic key", "ok", f"set, model {settings.model}")
        if settings.anthropic_api_key
        else CheckResult(
            "anthropic key", "warn", "ANTHROPIC_API_KEY is not set; run analyze with --no-agent"
        ),
        CheckResult("github token", "ok", "set")
        if settings.github_token
        else CheckResult("github token", "warn", "GITHUB_TOKEN is not set; ci/publish will fail"),
    ]

    if settings.datahub_mode == "mcp":
        found = shutil.which(settings.mcp_server_command)
        results.append(
            CheckResult("mcp server", "ok", f"{found}")
            if found
            else CheckResult(
                "mcp server",
                "warn",
                f"{settings.mcp_server_command!r} not on PATH; "
                "set BLAST_RADIUS_DATAHUB_MODE=sdk to use the Python SDK path",
            )
        )
        results.append(
            CheckResult("mcp mutations", "ok", f"{MUTATION_ENV_VAR}=true")
            if settings.mutation_enabled
            else CheckResult(
                "mcp mutations",
                "warn",
                f"{MUTATION_ENV_VAR} is not true; write-back will fall back to the Python SDK "
                f"(mutation tools need mcp-server-datahub v{MIN_MCP_SERVER_VERSION}+)",
            )
        )
    return results


def check_write_path(settings: Settings) -> CheckResult:
    """Probe the write path, reporting honestly when the probe is not written yet."""
    try:
        capabilities = detect(
            gms_url=settings.datahub_gms_url,
            mcp_server_command=settings.mcp_server_command,
            mutation_env_flag=settings.mutation_enabled,
        )
    except NotImplementedError as exc:
        return CheckResult("write path", "not_implemented", str(exc).splitlines()[0], OWNER_A)
    status: CheckStatus = "ok" if capabilities.can_write else "fail"
    return CheckResult("write path", status, capabilities.explain())


def check_write_round_trip(settings: Settings) -> CheckResult:
    """Write a record to a scratch entity and read it back.

    Contract for OWNER A: this is the check that actually earns the command.
    Write the `io.blastradius.impactRecord` structured property onto a
    dedicated, clearly-named scratch dataset URN, read it back, compare, and
    delete it. It must never touch a real dataset, and it must clean up even
    when the comparison fails.
    """
    raise StubNotImplementedError(
        f"{_T}.check_write_round_trip",
        OWNER_A,
        "write the structured property to a scratch URN, read it back, compare, clean up",
    )


def run_checks(settings: Settings) -> tuple[CheckResult, ...]:
    """Run every diagnostic and return the results in display order."""
    results: list[CheckResult] = [check_python(), check_contracts(), check_fixtures()]
    results.extend(check_environment(settings))
    results.append(check_write_path(settings))
    try:
        results.append(check_write_round_trip(settings))
    except NotImplementedError as exc:
        results.append(
            CheckResult("write round trip", "not_implemented", str(exc).splitlines()[0], OWNER_A)
        )
    return tuple(results)


def exit_code_for(results: tuple[CheckResult, ...]) -> int:
    """0 when everything works, 1 on a hard failure, 2 when work remains.

    `not_implemented` deliberately does not exit 0: during the build, a green
    doctor must mean the write path genuinely works.
    """
    if any(r.status == "fail" for r in results):
        return 1
    if any(r.status == "not_implemented" for r in results):
        return 2
    return 0
