"""The stub inventory keeps the scaffold honest.

Constraint 4 of the project: no mock implementations anywhere; stubs raise
NotImplementedError with a docstring describing the intended contract. These
tests enforce that mechanically, so it stays true as both owners fill the
scaffold in.
"""

from __future__ import annotations

from pathlib import Path

from core.stubs import (
    UNOWNED_LABEL,
    find_stubs,
    group_by_owner,
    iter_source_files,
    owners_for,
    parse_codeowners,
    repo_root,
)

ROOT = repo_root()
STUBS = find_stubs(ROOT)
CODEOWNERS = parse_codeowners(ROOT / ".github" / "CODEOWNERS")


def test_the_scanner_finds_stubs() -> None:
    """A scanner that finds nothing would make every other test here vacuous."""
    assert len(STUBS) >= 20


def test_every_stub_documents_its_contract() -> None:
    """A stub without a docstring is a landmine for whoever picks it up."""
    undocumented = [s.location for s in STUBS if s.contract.startswith("(no docstring")]
    assert not undocumented, f"stubs missing a contract docstring: {undocumented}"


def test_every_stub_has_an_owner() -> None:
    """Unowned work does not get done."""
    unowned = [s.location for s in STUBS if s.owner_label == UNOWNED_LABEL]
    assert not unowned, f"stubs not covered by CODEOWNERS: {unowned}"


def test_work_is_split_between_both_owners() -> None:
    """If one owner has everything, the two-owner model is decorative."""
    grouped = group_by_owner(STUBS)
    assert any("etka" in label for label in grouped), "owner A has no stubs"
    assert any("teammate" in label for label in grouped), "owner B has no stubs"


def test_codeowners_covers_every_source_directory() -> None:
    """Every top-level directory must map to an owner, or a PR merges unreviewed."""
    top_level = {
        path.relative_to(ROOT).parts[0]
        for path in iter_source_files(ROOT)
        if path.relative_to(ROOT).parts[0] not in {"."}
    }
    uncovered = [d for d in sorted(top_level) if not owners_for(f"{d}/x.py", CODEOWNERS)]
    assert not uncovered, f"directories with no CODEOWNERS rule: {uncovered}"


def test_core_belongs_to_owner_a_and_ci_to_owner_b() -> None:
    """The ownership table in CLAUDE.md and CODEOWNERS must agree."""
    assert owners_for("core/severity/rules.py", CODEOWNERS) == ("@etka",)
    assert owners_for("skill/README.md", CODEOWNERS) == ("@etka",)
    assert owners_for("ci/diff/extract.py", CODEOWNERS) == ("@teammate",)
    assert owners_for("env/seed_demo.py", CODEOWNERS) == ("@teammate",)


def test_contracts_require_both_owners() -> None:
    """The frozen interface cannot be changed unilaterally."""
    owners = owners_for("contracts/change_set.schema.json", CODEOWNERS)
    assert set(owners) == {"@etka", "@teammate"}, owners


def test_no_mock_or_fake_implementations() -> None:
    """Constraint 4, checked by grep rather than by trust.

    A scaffold that quietly returns plausible data is worse than one that
    raises: the demo passes, the numbers are fiction, and nobody finds out
    until someone depends on them.
    """
    banned = ("return _FAKE", "FAKE_RESPONSE", "MOCK_RESPONSE", "# mock implementation")
    offenders: list[str] = []
    for path in iter_source_files(ROOT):
        if "tests" in path.parts:
            continue
        content = path.read_text(encoding="utf-8")
        offenders.extend(
            f"{path.relative_to(ROOT)}: {marker}" for marker in banned if marker in content
        )
    assert not offenders, offenders


def test_stub_locations_are_real_files() -> None:
    for stub in STUBS:
        assert (ROOT / Path(stub.file)).is_file(), stub.file
        assert stub.line > 0
