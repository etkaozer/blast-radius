"""Editing `schema.yml` without disturbing the rest of it.

The demo plants a description whose content-addressed id is a hash of exactly
those bytes. An editor that quietly wrote something slightly different — a
folded newline, a stripped trailing space — would break the correspondence
between the demo and `contracts/fixtures/03_adversarial_description`, and
nothing else in the project would notice. So these tests are about bytes.

Only `contracts` and `env` are imported. Nothing here touches `core/`.
"""

from __future__ import annotations

import pytest

from env.schema_yaml import (
    SchemaEditError,
    read_column_description,
    read_column_meta,
    set_column_description_and_meta,
)
from env.seed_demo import ADVERSARIAL_DESCRIPTION, ADVERSARIAL_META, adversarial_meta_mapping

SOURCE = """\
version: 2

models:
  - name: stg_customers
    description: One row per customer, straight from the raw feed.
    columns:
      - name: customer_id
        description: Surrogate key from the source system.
        tests: [unique, not_null]
      - name: signup_channel
        # This comment explains that the adversarial text is a demo fixture and
        # not our own documentation. Losing it would be a small disaster.
        description: How the customer first arrived.
        meta:
          owner: growth-team
      - name: signup_date
        description: Date the customer account was created.

  - name: stg_orders
    columns:
      - name: order_id
        description: Untouched.
"""


def plant(source: str = SOURCE) -> str:
    return set_column_description_and_meta(
        source, "signup_channel", ADVERSARIAL_DESCRIPTION, adversarial_meta_mapping()
    )


# --------------------------------------------------------------------------
# the bytes
# --------------------------------------------------------------------------


def test_a_multiline_description_round_trips_exactly() -> None:
    """`|-` and not `>`: a folded scalar rewrites the newline as a space, and
    the id is a hash of the newline too."""
    assert read_column_description(plant(), "stg_customers", "signup_channel") == (
        ADVERSARIAL_DESCRIPTION
    )


def test_the_meta_mapping_flattens_back_to_the_fixture_string() -> None:
    """The fixture holds meta flattened; schema.yml holds it as a mapping. If
    the two stop agreeing, the planted text and the fixture drift apart."""
    meta = read_column_meta(plant(), "stg_customers", "signup_channel")
    assert "; ".join(f"{key}: {value}" for key, value in meta.items()) == ADVERSARIAL_META


def test_planting_twice_produces_the_same_file() -> None:
    """Seeding is idempotent; running it twice must not produce two of anything."""
    assert plant(plant()) == plant()


# --------------------------------------------------------------------------
# everything else stays where it was
# --------------------------------------------------------------------------


def test_the_comment_explaining_the_demo_survives() -> None:
    """A project whose subject is misleading text in a catalog cannot afford to
    drop the comment saying this text is a fixture."""
    assert "not our own documentation" in plant()


def test_the_other_columns_are_untouched() -> None:
    planted = plant()
    assert "Surrogate key from the source system." in planted
    assert "Date the customer account was created." in planted
    assert read_column_description(planted, "stg_orders", "order_id") == "Untouched."


def test_the_model_description_is_untouched() -> None:
    document = plant()
    assert "One row per customer, straight from the raw feed." in document


def test_tests_declared_on_a_sibling_column_survive() -> None:
    assert "tests: [unique, not_null]" in plant()


# --------------------------------------------------------------------------
# failure
# --------------------------------------------------------------------------


def test_a_column_that_is_not_there_is_an_error_not_a_silent_no_op() -> None:
    """Silently planting nothing would leave a demo that shows no attack."""
    with pytest.raises(SchemaEditError, match="no column named"):
        set_column_description_and_meta(SOURCE, "nonexistent", "x", {})


def test_a_column_with_no_description_yet_gains_one() -> None:
    source = SOURCE.replace("        description: How the customer first arrived.\n", "")
    assert read_column_description(plant(source), "stg_customers", "signup_channel") == (
        ADVERSARIAL_DESCRIPTION
    )
