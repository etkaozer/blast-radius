"""OWNER B's own tests for the decisions `ci/render` had to make.

`test_contracts_owner_b.py` holds the acceptance criteria. This file covers the
rendering choices made while satisfying them — above all the ones that protect a
reader: an owner handle from a writable catalog must not be able to ping an
organisation, and untrusted text must reach the comment intact without being
able to restructure it.

Only `contracts` and `ci` are imported. Nothing here touches `core/`.
"""

from __future__ import annotations

import pytest

from ci.render.markdown import (
    COMMENT_MARKER,
    quote_untrusted,
    render_comment,
    render_severity_table,
)
from contracts.loader import fixture_dir, load_impact_report
from contracts.models import ImpactReport

FIXTURES = ("01_rename", "02_removal_contract", "03_adversarial_description")


def report(name: str) -> ImpactReport:
    return load_impact_report(fixture_dir(name) / "expected_impact_report.json")


# --------------------------------------------------------------------------
# quoting
# --------------------------------------------------------------------------


def test_html_cannot_reach_the_comment_as_markup() -> None:
    """Including a copy of our own marker, which would confuse the publisher
    into updating the wrong comment."""
    quoted = quote_untrusted(f"{COMMENT_MARKER}<img src=x onerror=alert(1)>")
    assert "<img" not in quoted
    assert COMMENT_MARKER not in quoted
    assert "&lt;img src=x onerror=alert(1)&gt;" in quoted


def test_every_line_is_inside_the_blockquote() -> None:
    """A passage that escaped its quote could forge a section of the comment."""
    quoted = quote_untrusted("first\n\n## Forged heading\nlast")
    assert all(line.startswith(">") for line in quoted.splitlines())


def test_backticks_cannot_close_a_code_fence() -> None:
    quoted = quote_untrusted("```\nnot a fence\n```")
    assert "\\`\\`\\`" in quoted


def test_the_text_a_human_reads_is_unchanged() -> None:
    """Neutralising a mention must not cost the reader a single character."""
    quoted = quote_untrusted("ping @data-platform now")
    assert quoted.replace("​", "") == "> ping @data-platform now"


# --------------------------------------------------------------------------
# owners
# --------------------------------------------------------------------------


def test_a_handle_shaped_like_an_attack_is_not_mentioned() -> None:
    """Ownership comes from DataHub, which anyone with an ingestion pipeline can
    write to. A handle is only turned into a real mention if it looks like one."""
    payload = report("01_rename").model_dump()
    payload["column_impacts"][0]["owners_to_notify"][0]["handle"] = "@everyone please approve"
    body = render_comment(ImpactReport.model_validate(payload))
    assert "@everyone" not in body
    assert "Dana Eng" in body


def test_a_well_formed_handle_is_mentioned() -> None:
    assert "@dana-eng" in render_comment(report("01_rename"))


# --------------------------------------------------------------------------
# severity
# --------------------------------------------------------------------------


def test_the_table_shows_the_factors_that_moved_the_score() -> None:
    table = render_severity_table(report("02_removal_contract"))
    assert "change_kind_risk removed → +30" in table
    assert "sev-v1" in table
    assert "deterministic" in table


def test_factors_that_contributed_nothing_are_not_listed_as_drivers() -> None:
    """Fixture 03 has contract_presence at zero. Listing it would suggest a
    contract exists."""
    table = render_severity_table(report("03_adversarial_description"))
    assert "contract_presence" not in table


# --------------------------------------------------------------------------
# whole comment
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", FIXTURES)
def test_every_fixture_renders_deterministically(name: str) -> None:
    assert render_comment(report(name)) == render_comment(report(name))


@pytest.mark.parametrize("name", FIXTURES)
def test_the_marker_comes_first(name: str) -> None:
    """`ci/publish` finds its previous comment by this marker."""
    assert render_comment(report(name)).startswith(COMMENT_MARKER)


@pytest.mark.parametrize("name", FIXTURES)
def test_the_rule_version_is_always_named(name: str) -> None:
    """The weights are a judgement call; a reader is entitled to disagree with a
    specific version of them."""
    assert "sev-v1" in render_comment(report(name))


def test_a_degraded_report_says_what_it_could_not_measure() -> None:
    """A report that could not read usage must not look like one that found none."""
    payload = report("01_rename").model_dump()
    payload["degradations"] = [
        {
            "capability": "query_usage",
            "reason": "DataHub usage aspect was empty",
            "consequence": "The usage factor scored zero.",
        }
    ]
    payload["column_impacts"][0]["query_usage"] = {
        "window_days": 30,
        "query_count": 0,
        "source": "unavailable",
    }
    body = render_comment(ImpactReport.model_validate(payload))
    assert "not available" in body
    assert "floor" in body
    assert "query_usage" in body


def test_a_report_with_no_explanation_still_renders() -> None:
    """`--no-agent` produces a useful report without an API key."""
    payload = report("01_rename").model_dump()
    payload["column_impacts"][0]["explanation"] = None
    body = render_comment(ImpactReport.model_validate(payload))
    assert "Model-generated prose" not in body
    assert "blast radius" in body


def test_a_fix_that_failed_never_appears_as_a_verified_one() -> None:
    """Fixture 02 generates two fixes; only one compiled. The one that did not
    must not be reachable from the list a reader would merge from."""
    _, _, fixes = render_comment(report("02_removal_contract")).partition("## Candidate fixes")
    verified, _, failed = fixes.partition("<details>")
    assert "customer_ltv.sql" in verified
    assert "mart_exec_summary" not in verified
    assert "mart_exec_summary" in failed
    assert "did not compile" in failed
