"""Invariants about the repository itself.

Shared, because both owners depend on them. These are the checks that catch the
kind of drift nobody notices until it costs an afternoon: a version bumped in
one place, an ownership table that no longer matches CODEOWNERS, a settings
profile that stops blocking what it claims to block.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from contracts.version import VERSION

ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------


def test_version_matches_pyproject() -> None:
    match = re.search(r'^version\s*=\s*"([^"]+)"', read("pyproject.toml"), re.MULTILINE)
    assert match, "pyproject.toml has no project version"
    assert match.group(1) == VERSION


# ---------------------------------------------------------------------------
# Licensing
# ---------------------------------------------------------------------------


def test_license_is_apache_2_and_detectable() -> None:
    """GitHub detects the license from the canonical text; keep it canonical."""
    text = read("LICENSE")
    assert "Apache License" in text
    assert "Version 2.0, January 2004" in text
    assert "http://www.apache.org/licenses/LICENSE-2.0" in text
    assert "TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION" in text


def test_pyproject_declares_the_same_license() -> None:
    assert 'license = "Apache-2.0"' in read("pyproject.toml")


# ---------------------------------------------------------------------------
# Ownership documentation
# ---------------------------------------------------------------------------


def test_agents_md_and_claude_md_agree() -> None:
    """Two names for one set of rules; they must not drift apart."""
    claude = read("CLAUDE.md")
    agents = read("AGENTS.md")
    # AGENTS.md adds a pointer paragraph at the top and is otherwise identical.
    assert claude.split("## Ownership table", 1)[1] == agents.split("## Ownership table", 1)[1]


@pytest.mark.parametrize("path", ["CLAUDE.md", "AGENTS.md"])
def test_root_instructions_carry_the_boundary_rule(path: str) -> None:
    """The exact instruction the project requires agents to follow."""
    text = read(path)
    assert "Before editing any file, check the ownership table" in text
    assert "stop and tell the user instead of editing" in text


@pytest.mark.parametrize("path", ["core", "ci", "env", "skill"])
def test_each_directory_has_its_own_instructions(path: str) -> None:
    text = read(f"{path}/CLAUDE.md")
    assert "## Scope" in text
    assert "## What this directory may import" in text
    assert "must NOT read" in text


@pytest.mark.parametrize(
    ("directory", "forbidden"),
    [("core", ["ci/", "env/"]), ("ci", ["core/", "skill/"]), ("env", ["core/", "skill/"])],
)
def test_directory_instructions_name_what_they_may_not_read(
    directory: str, forbidden: list[str]
) -> None:
    section = read(f"{directory}/CLAUDE.md").split("must NOT read", 1)[1]
    for item in forbidden:
        assert item in section, f"{directory}/CLAUDE.md does not forbid {item}"


# ---------------------------------------------------------------------------
# Claude Code settings profiles
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("profile", "blocked"),
    [
        ("settings.core.json", ["ci", "env"]),
        ("settings.ci.json", ["core", "skill"]),
    ],
)
def test_settings_profile_denies_the_other_half(profile: str, blocked: list[str]) -> None:
    settings = json.loads(read(f".claude/{profile}"))
    deny = settings["permissions"]["deny"]
    for directory in blocked:
        assert f"Read({directory}/**)" in deny, f"{profile} does not deny reading {directory}/"
        assert f"Edit({directory}/**)" in deny, f"{profile} does not deny editing {directory}/"


@pytest.mark.parametrize("profile", ["settings.core.json", "settings.ci.json"])
def test_settings_profiles_use_only_consulted_rule_types(profile: str) -> None:
    """Only Read(path) and Edit(path) rules are checked for file access.

    A `Write(path)` rule is accepted and then never consulted, and Claude Code
    warns about it at startup. Writing one is the classic mistake in a
    hand-written profile.
    """
    settings = json.loads(read(f".claude/{profile}"))
    rules = settings["permissions"].get("deny", []) + settings["permissions"].get("ask", [])
    for rule in rules:
        tool = rule.split("(", 1)[0]
        assert tool in {"Read", "Edit"}, f"{profile}: {rule} uses a rule type that is not consulted"


@pytest.mark.parametrize("profile", ["settings.core.json", "settings.ci.json"])
def test_settings_profiles_are_valid_json_with_a_schema(profile: str) -> None:
    settings = json.loads(read(f".claude/{profile}"))
    assert settings["$schema"].endswith("claude-code-settings.json")


def test_settings_local_json_is_gitignored() -> None:
    """Each developer's copy must never be committed."""
    assert ".claude/settings.local.json" in read(".gitignore")


# ---------------------------------------------------------------------------
# Secrets hygiene
# ---------------------------------------------------------------------------


def test_env_example_has_placeholders_not_values() -> None:
    text = read(".env.example")
    for key in ("DATAHUB_GMS_URL", "DATAHUB_GMS_TOKEN", "ANTHROPIC_API_KEY", "GITHUB_TOKEN"):
        assert key in text, f"{key} missing from .env.example"
    assert "REPLACE_WITH_" in text
    assert "sk-ant-" not in text, "a real-looking Anthropic key is in .env.example"


def test_env_is_gitignored() -> None:
    gitignore = read(".gitignore")
    assert re.search(r"^\.env$", gitignore, re.MULTILINE)


# ---------------------------------------------------------------------------
# The demo and the fixtures must tell the same story
# ---------------------------------------------------------------------------


def test_seeded_adversarial_text_matches_the_fixture_byte_for_byte() -> None:
    """The live demo and the test suite must show the identical attack.

    The text's id is a hash of exactly these bytes. If the seeding script and
    the fixture drift — a reworded sentence, a stray trailing space — the demo
    stops demonstrating the thing the tests prove, and nothing else would
    notice.

    This test is the reason it is safe for `env/` and `contracts/` to be owned
    by different people.
    """
    from contracts.loader import fixture_dir, load_change_set

    # env/ is OWNER B's, contracts/ is shared: this test reads the constant as
    # text rather than importing it, so it stays a contract check rather than a
    # cross-package dependency.
    seed_source = read("env/seed_demo.py")
    change_set = load_change_set(fixture_dir("03_adversarial_description") / "change_set.json")

    description = next(
        text.value
        for text in change_set.all_untrusted_text()
        if text.source == "dbt_yaml_description"
    )
    meta = next(
        text.value for text in change_set.all_untrusted_text() if text.source == "dbt_yaml_meta"
    )

    for line in description.splitlines():
        assert line in seed_source, f"env/seed_demo.py no longer plants: {line!r}"
    assert meta in seed_source, "env/seed_demo.py no longer plants the adversarial meta value"
