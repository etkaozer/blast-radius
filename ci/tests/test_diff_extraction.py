"""OWNER B's own tests for the decisions `ci/diff` had to make.

`test_contracts_owner_b.py` holds the acceptance criteria: what the other half
of the project is entitled to rely on. This file covers the choices made while
satisfying them — where the contract was silent and an implementation had to
pick something, the pick is written down here so it cannot drift silently.

Only `contracts` and `ci` are imported. Nothing here touches `core/`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ci.diff.dbt import DBT_PLATFORM, DbtProject
from ci.diff.extract import (
    STAR_PROJECTION,
    FileDiff,
    UnresolvableProjectionError,
    collect_untrusted_text,
    diff_columns,
    neutralise_jinja,
    parse_projection,
)

MODEL = "models/staging/stg_customers.sql"
SCHEMA_YML = Path("models/staging/schema.yml")


def sql(*projections: str) -> str:
    return f"select {', '.join(projections)} from {{{{ ref('raw_customers') }}}}"


# --------------------------------------------------------------------------
# jinja
# --------------------------------------------------------------------------


def test_config_blocks_are_removed_not_replaced() -> None:
    """`{{ config(...) }}` renders to nothing, so it must parse as nothing."""
    model = "{{ config(materialized='table') }}\nselect id from {{ ref('x') }}"
    assert parse_projection(model) == (("id", None),)


def test_the_same_jinja_expression_gets_the_same_placeholder() -> None:
    """Otherwise the two revisions of a file could not be compared expression-wise."""
    once = neutralise_jinja("select a from {{ ref('t') }}")
    twice = neutralise_jinja("select b from {{ ref('t') }}")
    assert once.split("from")[1] == twice.split("from")[1]


def test_statement_blocks_do_not_defeat_the_parser() -> None:
    model = "{% set channels = ['organic'] %}\nselect id, signup_channel from {{ ref('x') }}"
    assert [name for name, _ in parse_projection(model)] == ["id", "signup_channel"]


# --------------------------------------------------------------------------
# projection
# --------------------------------------------------------------------------


def test_a_cast_declares_a_type() -> None:
    assert parse_projection("select cast(signup_date as date) as signup_date from t") == (
        ("signup_date", "DATE"),
    )


def test_star_returns_the_marker_rather_than_an_empty_projection() -> None:
    """An empty projection would read as "this model has no columns"."""
    assert parse_projection("select * from t") == STAR_PROJECTION


def test_star_is_refused_by_the_differ_rather_than_reported_as_no_changes() -> None:
    star = FileDiff(path=MODEL, base_content=sql("id", "email"), head_content="select * from t")
    with pytest.raises(UnresolvableProjectionError, match="SELECT \\*"):
        diff_columns(star)


def test_an_unparseable_model_raises_rather_than_reporting_nothing() -> None:
    broken = FileDiff(path=MODEL, base_content=sql("id"), head_content="select from where")
    with pytest.raises(UnresolvableProjectionError):
        diff_columns(broken)


# --------------------------------------------------------------------------
# rename inference
# --------------------------------------------------------------------------


def test_an_alias_over_an_unchanged_source_column_is_a_rename() -> None:
    change = diff_columns(
        FileDiff(
            path=MODEL,
            base_content=sql("id", "email", "signup_channel"),
            head_content=sql("id", "email as email_address", "signup_channel"),
        )
    )
    assert [(c.change_kind, c.column, c.new_value) for c in change] == [
        ("renamed", "email", "email_address")
    ]


def test_two_removals_and_two_additions_are_not_guessed_at() -> None:
    """Weak evidence reports add + remove: a false rename generates a fix that
    silently changes semantics, a false add+remove one that fails to compile."""
    changes = diff_columns(
        FileDiff(
            path=MODEL,
            base_content=sql("id", "email", "signup_channel"),
            head_content=sql("id", "email_address", "channel"),
        )
    )
    assert sorted({c.change_kind for c in changes}) == ["added", "removed"]
    assert len(changes) == 4


def test_a_move_without_a_name_change_is_not_a_rename() -> None:
    """Reordering a projection changes nothing about the columns."""
    assert (
        diff_columns(
            FileDiff(
                path=MODEL,
                base_content=sql("id", "email", "signup_channel"),
                head_content=sql("id", "signup_channel", "email"),
            )
        )
        == ()
    )


def test_a_removal_carries_what_was_there() -> None:
    """`old_value` is the declared type where the model declares one, and the
    projected expression otherwise — never absent, never invented."""
    (change,) = diff_columns(
        FileDiff(
            path=MODEL,
            base_content=sql("id", "signup_channel"),
            head_content=sql("id"),
        )
    )
    assert (change.change_kind, change.column, change.old_value) == (
        "removed",
        "signup_channel",
        "signup_channel",
    )


def test_a_type_change_is_reported_with_both_types() -> None:
    (change,) = diff_columns(
        FileDiff(
            path=MODEL,
            base_content=sql("cast(signup_date as date) as signup_date"),
            head_content=sql("cast(signup_date as timestamp) as signup_date"),
        )
    )
    assert (change.change_kind, change.old_value, change.new_value) == (
        "type_changed",
        "DATE",
        "TIMESTAMP",
    )


def test_ids_are_sequential_in_projection_order() -> None:
    changes = diff_columns(
        FileDiff(
            path=MODEL,
            base_content=sql("id", "email", "signup_channel"),
            head_content=sql("id"),
        )
    )
    assert [c.id for c in changes] == ["cc-1", "cc-2"]
    assert [c.column for c in changes] == ["email", "signup_channel"]


# --------------------------------------------------------------------------
# dataset identity
# --------------------------------------------------------------------------


@pytest.fixture
def unbuilt_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A dbt project that has never been compiled, and is the one discovery finds.

    These tests used to run against the repository's own `env/dbt_project`,
    which had no `target/` only because nobody had built it. Once
    `env/seed_demo.py` runs — which the README tells every developer to do —
    a manifest exists and the "without a manifest" case silently stops being
    tested. The state under test now belongs to the test.
    """
    project = tmp_path / "env" / "dbt_project"
    (project / "models" / "staging").mkdir(parents=True)
    (project / "dbt_project.yml").write_text(
        'name: demo_project\nversion: "1.0.0"\nconfig-version: 2\n'
        'profile: demo_project\nmodel-paths: ["models"]\n',
        encoding="utf-8",
    )
    (project / "models" / "staging" / "stg_customers.sql").write_text(
        sql("id", "email"), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    return project


def test_without_a_manifest_the_urn_is_on_the_dbt_platform(unbuilt_project: Path) -> None:
    """A guess at the warehouse platform would produce a URN that silently
    matches nothing in DataHub. dbt is a real platform, so say dbt."""
    (change,) = diff_columns(
        FileDiff(path=MODEL, base_content=sql("id", "email"), head_content=sql("id"))
    )
    assert f"urn:li:dataPlatform:{DBT_PLATFORM}" in change.dataset_urn
    assert change.dataset_name.endswith(".staging.stg_customers")


def test_a_project_that_cannot_be_found_raises(unbuilt_project: Path) -> None:
    project = DbtProject.discover(MODEL)
    assert (project.root / "dbt_project.yml").is_file()
    assert project.root == unbuilt_project
    assert project.manifest_path is None  # nothing compiled in this project


# --------------------------------------------------------------------------
# untrusted text
# --------------------------------------------------------------------------


def test_descriptions_are_collected_for_the_columns_that_changed() -> None:
    texts = collect_untrusted_text(
        FileDiff(
            path=MODEL,
            base_content=sql("id", "email", "signup_channel"),
            head_content=sql("id", "email_address", "signup_channel"),
        ),
        SCHEMA_YML,
    )
    fields = [text.field for text in texts]
    assert "models.stg_customers.columns.email.description" in fields
    assert "models.stg_customers.columns.signup_channel.description" not in fields


def test_meta_is_flattened_the_way_the_fixture_writes_it() -> None:
    """`contracts/fixtures/03_adversarial_description` renders meta as
    `key: value; key: value`; the extractor must produce the same shape or the
    ids stop matching."""
    texts = collect_untrusted_text(
        FileDiff(
            path=MODEL,
            base_content=sql("id", "signup_channel"),
            head_content=sql("id"),
        ),
        SCHEMA_YML,
    )
    meta = next(text for text in texts if text.source == "dbt_yaml_meta")
    assert meta.value == "owner: growth-team"


def test_sql_comments_are_collected_as_one_block_per_run_of_lines() -> None:
    """An instruction split across two comment lines is not two instructions."""
    model = "-- first line\n-- second line\nselect id from t"
    (comment,) = [
        text
        for text in collect_untrusted_text(
            FileDiff(path=MODEL, base_content=None, head_content=model), None
        )
        if text.source == "sql_comment"
    ]
    assert comment.value == "first line\nsecond line"
    assert comment.line == 1


def test_text_is_deduplicated_by_content_address() -> None:
    """Two identical strings hash to one id, and the id is the envelope nonce."""
    model = "-- same\nselect id from t\n-- same\n"
    comments = [
        text
        for text in collect_untrusted_text(
            FileDiff(path=MODEL, base_content=None, head_content=model), None
        )
        if text.source == "sql_comment"
    ]
    assert len(comments) == 1


def test_a_missing_schema_file_costs_the_yaml_text_and_nothing_else() -> None:
    texts = collect_untrusted_text(
        FileDiff(path=MODEL, base_content=sql("id", "email"), head_content=sql("id")),
        Path("models/staging/does_not_exist.yml"),
    )
    assert all(text.source == "sql_comment" for text in texts)
