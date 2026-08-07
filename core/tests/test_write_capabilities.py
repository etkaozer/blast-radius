"""Tests for the write-path probe.

The failure `blast-radius doctor` exists to prevent is a team wiring this into
CI, watching it post good comments for a week, and discovering on the day
someone needs the history that nothing was ever written back. That only works if
the probe is right about a broken environment AND never wrong about a working
one — so the tests below are mostly about the ways a probe lies:

* by comparing versions as strings, which makes 0.10.0 look older than 0.5.0;
* by raising, which turns a diagnostic command into another thing to debug;
* by inferring a DataHub Cloud feature from a hostname.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from core.writeback import capabilities
from core.writeback.capabilities import (
    MIN_MCP_SERVER_VERSION,
    detect,
    meets_minimum,
    probe_gms,
    probe_mcp_version,
    version_tuple,
)


def fake_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, script: str) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    executable = bin_dir / name
    executable.write_text(script, encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


# ---------------------------------------------------------------------------
# Version comparison
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0.5.0", (0, 5, 0)),
        ("mcp-server-datahub 0.10.2", (0, 10, 2)),
        ("v1.2.3-rc1", (1, 2, 3)),
        ("no version here", None),
        ("", None),
        (None, None),
    ],
)
def test_versions_are_parsed_out_of_whatever_the_server_prints(
    raw: str | None, expected: tuple[int, int, int] | None
) -> None:
    assert version_tuple(raw) == expected


def test_ten_is_newer_than_five() -> None:
    """String ordering says otherwise, and would disable a working write path."""
    assert meets_minimum("0.10.0") is True
    assert "0.10.0" < "0.5.0"  # the bug this guards against


@pytest.mark.parametrize(
    ("found", "expected"),
    [("0.5.0", True), ("0.5.1", True), ("1.0.0", True), ("0.4.9", False), (None, False)],
)
def test_the_minimum_is_enforced_numerically(found: str | None, expected: bool) -> None:
    assert meets_minimum(found, MIN_MCP_SERVER_VERSION) is expected


# ---------------------------------------------------------------------------
# Probing, and never raising
# ---------------------------------------------------------------------------


def test_a_missing_server_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    version, note = probe_mcp_version("mcp-server-datahub")
    assert version is None
    assert note is not None
    assert "PATH" in note


def test_a_server_that_reports_a_version_is_believed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_command(tmp_path, monkeypatch, "mcp-server-datahub", "#!/bin/sh\necho '0.7.3'\n")
    version, note = probe_mcp_version("mcp-server-datahub")
    assert version == "0.7.3"
    assert note is None


def test_a_server_that_reports_nothing_useful_is_not_guessed_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_command(tmp_path, monkeypatch, "mcp-server-datahub", "#!/bin/sh\necho 'hello'\n")
    version, note = probe_mcp_version("mcp-server-datahub")
    assert version is None
    assert note is not None


def test_a_crashing_server_does_not_take_doctor_with_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_command(tmp_path, monkeypatch, "mcp-server-datahub", "#!/bin/sh\nexit 3\n")
    version, _ = probe_mcp_version("mcp-server-datahub")
    assert version is None


@pytest.mark.parametrize("url", ["", "not-a-url", "ftp://example.com", "localhost:8080"])
def test_a_nonsense_gms_url_is_rejected_without_a_request(url: str) -> None:
    reachable, note = probe_gms(url)
    assert reachable is False
    assert note is not None


def test_an_unreachable_gms_is_reported() -> None:
    """Port 1 is reserved and nothing listens on it."""
    reachable, note = probe_gms("http://127.0.0.1:1")
    assert reachable is False
    assert note is not None and "unreachable" in note


# ---------------------------------------------------------------------------
# The whole probe
# ---------------------------------------------------------------------------


def test_detect_never_raises_in_a_completely_broken_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "")
    capabilities = detect("not-a-url", "mcp-server-datahub", mutation_env_flag=False)

    assert capabilities.can_write is False
    assert capabilities.preferred_path == "none"
    assert capabilities.notes


def test_mutations_need_both_a_new_enough_server_and_the_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_command(tmp_path, monkeypatch, "mcp-server-datahub", "#!/bin/sh\necho '0.6.0'\n")

    without_flag = detect("http://127.0.0.1:1", "mcp-server-datahub", mutation_env_flag=False)
    with_flag = detect("http://127.0.0.1:1", "mcp-server-datahub", mutation_env_flag=True)

    assert without_flag.mcp_available is True
    assert without_flag.mcp_mutations_enabled is False
    assert with_flag.mcp_mutations_enabled is True
    assert with_flag.preferred_path == "mcp"


def test_an_old_server_does_not_enable_mutations_even_with_the_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_command(tmp_path, monkeypatch, "mcp-server-datahub", "#!/bin/sh\necho '0.4.0'\n")
    capabilities = detect("http://127.0.0.1:1", "mcp-server-datahub", mutation_env_flag=True)

    assert capabilities.mcp_available is False
    assert capabilities.mcp_mutations_enabled is False
    assert any("older than" in note for note in capabilities.notes)


def test_the_sdk_path_needs_a_reachable_gms(monkeypatch: pytest.MonkeyPatch) -> None:
    """acryl-datahub installed is not the same as acryl-datahub usable."""
    monkeypatch.setenv("PATH", "")
    without = detect("http://127.0.0.1:1", "mcp-server-datahub", False, token=None)
    assert without.sdk_available is False
    assert any("unreachable" in note for note in without.notes)


def test_a_missing_token_does_not_remove_the_write_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An open-source quickstart issues no token and still accepts writes.

    Requiring one reported "no write path available" on exactly the deployment
    the demo runs on, which turned a working write-back into a silent
    degradation. The absence is still reported, as a note rather than as a
    missing capability.
    """
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(capabilities, "probe_gms", lambda _url: (True, None))

    without = detect("http://localhost:8080", "mcp-server-datahub", False, token=None)

    assert without.sdk_available is True
    assert without.preferred_path == "sdk"
    assert any("DATAHUB_GMS_TOKEN is not set" in note for note in without.notes)


def test_proposals_are_never_inferred_from_the_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """A DataHub Cloud hostname is not evidence of a DataHub Cloud feature."""
    monkeypatch.setenv("PATH", "")
    cloud = detect("https://acme.acryl.io/gms", "mcp-server-datahub", True, token="t")

    assert cloud.proposals_available is False
    assert any("would be a guess" in note for note in cloud.notes)


def test_the_probe_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Probing by writing is how a doctor command pollutes a production catalog.

    The MCP server is invoked with `--version` and nothing else; this asserts
    the argv the probe actually used.
    """
    log = tmp_path / "argv.log"
    fake_command(
        tmp_path,
        monkeypatch,
        "mcp-server-datahub",
        f'#!/bin/sh\necho "$@" >> {log}\necho 0.9.0\n',
    )
    detect("http://127.0.0.1:1", "mcp-server-datahub", mutation_env_flag=True)

    assert log.read_text(encoding="utf-8").strip() == "--version"
