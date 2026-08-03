"""The pipeline's stage ORDER is a security control, so it is tested like one.

Severity is computed before any untrusted prose is read and before any model is
called. If a refactor reorders these stages the guarantee in the README stops
being true, and this file is what notices.
"""

from __future__ import annotations

import pytest

from core.pipeline import STAGES


def index_of(key: str) -> int:
    return next(i for i, stage in enumerate(STAGES) if stage.key == key)


def test_all_stages_are_uniquely_named() -> None:
    keys = [s.key for s in STAGES]
    assert len(set(keys)) == len(keys)


def test_untrusted_text_is_wrapped_before_anything_reads_it() -> None:
    assert index_of("wrap") < index_of("impact")
    assert index_of("wrap") < index_of("explain")


def test_severity_is_computed_before_untrusted_text_is_scanned() -> None:
    """Detection is reporting. It runs after the number is already fixed."""
    assert index_of("severity") < index_of("detect")


def test_severity_is_computed_before_the_model_is_called() -> None:
    """The headline ordering claim in the README."""
    assert index_of("severity") < index_of("explain")
    assert index_of("severity") < index_of("fixes")


def test_impact_is_grounded_before_severity_is_scored() -> None:
    assert index_of("impact") < index_of("severity")


def test_fixes_are_generated_after_the_explanation_stage() -> None:
    assert index_of("explain") < index_of("fixes")


def test_the_report_is_assembled_last() -> None:
    assert STAGES[-1].key == "report"


@pytest.mark.parametrize("stage", STAGES, ids=lambda s: s.key)
def test_every_stage_names_a_module_and_an_owner(stage: object) -> None:
    assert stage.module  # type: ignore[attr-defined]
    assert stage.owner  # type: ignore[attr-defined]
    assert stage.title  # type: ignore[attr-defined]
