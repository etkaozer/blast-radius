"""Tests for the grounding stage, against a reader that replays a golden fixture.

The interesting property is that `analyze_column` is a pure function of what the
reader said. So the strongest available test is to build a reader that says
exactly what `expected_impact_report.json` records, run the analyzer, and check
that the severity, the downstream set and the owner list come back identical.
That closes the loop between OWNER A's engine and the frozen fixture without a
DataHub anywhere near it.

The second half of the file is about failure. Degradation behaviour is not a
nicety here: "we could not measure the usage" and "nobody queries this column"
produce the same score, and the only thing that keeps them apart in the report
is a `Degradation` that this module is responsible for emitting.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from contracts.loader import fixture_dir, load_change_set, load_impact_report
from contracts.models import (
    AssertionRef,
    ColumnImpact,
    ContractRef,
    DownstreamEntity,
    EntityType,
    Owner,
    QueryUsage,
)
from core.config import Settings
from core.datahub.base import (
    DEFAULT_MAX_HOPS,
    DEFAULT_USAGE_WINDOW_DAYS,
    EntityRef,
    LineageDirection,
    LineagePath,
    ReaderNote,
    SchemaFieldInfo,
)
from core.errors import OWNER_A, DataHubAccessError, StubNotImplementedError
from core.impact.analyzer import analyze_change_set, analyze_column

SETTINGS = Settings()

FIXTURES = ["01_rename", "02_removal_contract", "03_adversarial_description"]


class ReplayReader:
    """A `DataHubReader` that answers from a fixture's expected impact report.

    Not a mock of DataHub: a recording of one. Every value it returns was
    written into the frozen fixture by hand and is the same value OWNER B's
    renderer is being built against.
    """

    def __init__(self, impact: ColumnImpact) -> None:
        self._impact = impact
        self.lineage_calls: list[tuple[str, str | None, int]] = []

    @property
    def access_path(self) -> str:
        return "sdk"

    def search(
        self,
        query: str,
        entity_types: Sequence[EntityType] | None = None,
        limit: int = 25,
    ) -> tuple[EntityRef, ...]:
        return ()

    def get_entities(self, urns: Sequence[str]) -> tuple[EntityRef, ...]:
        return ()

    def list_schema_fields(self, dataset_urn: str) -> tuple[SchemaFieldInfo, ...]:
        return ()

    def get_lineage(
        self,
        urn: str,
        column: str | None = None,
        direction: LineageDirection = "DOWNSTREAM",
        max_hops: int = DEFAULT_MAX_HOPS,
    ) -> tuple[DownstreamEntity, ...]:
        self.lineage_calls.append((urn, column, max_hops))
        return self._impact.downstream

    def get_lineage_paths_between(
        self,
        source_urn: str,
        target_urn: str,
        source_column: str | None = None,
        max_hops: int = DEFAULT_MAX_HOPS,
    ) -> tuple[LineagePath, ...]:
        return ()

    def get_dataset_queries(
        self,
        dataset_urn: str,
        window_days: int = DEFAULT_USAGE_WINDOW_DAYS,
    ) -> QueryUsage:
        return self._impact.query_usage

    def get_owners(self, urn: str) -> tuple[Owner, ...]:
        return tuple(o for o in self._impact.owners_to_notify if o.for_urn == urn)

    def get_assertions(
        self, dataset_urn: str, column: str | None = None
    ) -> tuple[AssertionRef, ...]:
        return self._impact.assertions

    def get_data_contracts(
        self, dataset_urn: str, column: str | None = None
    ) -> tuple[ContractRef, ...]:
        return self._impact.data_contracts


class BrokenReader(ReplayReader):
    """A reader whose every call fails the way a real one fails: at runtime."""

    def get_lineage(self, *args: object, **kwargs: object) -> tuple[DownstreamEntity, ...]:
        msg = "GMS returned 503"
        raise DataHubAccessError(msg)

    def get_dataset_queries(self, *args: object, **kwargs: object) -> QueryUsage:
        msg = "usage aspect not ingested"
        raise DataHubAccessError(msg)

    def get_owners(self, *args: object, **kwargs: object) -> tuple[Owner, ...]:
        msg = "ownership read timed out"
        raise DataHubAccessError(msg)

    def get_assertions(self, *args: object, **kwargs: object) -> tuple[AssertionRef, ...]:
        msg = "assertions unavailable"
        raise DataHubAccessError(msg)

    def get_data_contracts(self, *args: object, **kwargs: object) -> tuple[ContractRef, ...]:
        msg = "contracts unavailable"
        raise DataHubAccessError(msg)


def _raise_as_an_unwritten_reader_would() -> None:
    """Raise exactly what an unimplemented `DataHubReader` method raises.

    Built and then raised, rather than raised directly, so that the stub
    scanner in `core/stubs.py` does not count this test helper as outstanding
    project work. It is a test double for a stub, not a stub.
    """
    error = StubNotImplementedError(
        "core.datahub.sdk_client.SdkDataHubReader.get_lineage",
        OWNER_A,
        "column-level lineage traversal",
    )
    raise error


def fixture_pair(name: str) -> tuple[object, ColumnImpact]:
    directory = fixture_dir(name)
    change_set = load_change_set(directory / "change_set.json")
    report = load_impact_report(directory / "expected_impact_report.json")
    return change_set.column_changes[0], report.column_impacts[0]


# ---------------------------------------------------------------------------
# Replaying a fixture must reproduce the fixture.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", FIXTURES)
def test_replaying_a_fixture_reproduces_its_severity(name: str) -> None:
    """The whole engine, end to end, against the frozen numbers."""
    change, expected = fixture_pair(name)
    impact, _ = analyze_column(change, ReplayReader(expected), SETTINGS)  # type: ignore[arg-type]

    assert impact.severity.score == expected.severity.score
    assert impact.severity.level == expected.severity.level
    assert impact.severity.inputs_digest == expected.severity.inputs_digest
    assert impact.severity.computed_by == "deterministic"


@pytest.mark.parametrize("name", FIXTURES)
def test_replaying_a_fixture_reproduces_its_facts(name: str) -> None:
    change, expected = fixture_pair(name)
    impact, _ = analyze_column(change, ReplayReader(expected), SETTINGS)  # type: ignore[arg-type]

    assert impact.change_id == expected.change_id
    assert impact.change == expected.change
    assert impact.downstream == expected.downstream
    assert impact.owners_to_notify == expected.owners_to_notify
    assert impact.assertions == expected.assertions
    assert impact.data_contracts == expected.data_contracts
    assert impact.query_usage == expected.query_usage


@pytest.mark.parametrize("name", FIXTURES)
def test_the_analyzer_leaves_the_model_shaped_fields_empty(name: str) -> None:
    """Grounding is agent-free. Prose and findings are attached later, by the pipeline."""
    change, expected = fixture_pair(name)
    impact, _ = analyze_column(change, ReplayReader(expected), SETTINGS)  # type: ignore[arg-type]

    assert impact.explanation is None
    assert impact.untrusted_findings == ()
    assert impact.fix_ids == ()


def test_lineage_is_requested_column_level_and_bounded() -> None:
    """Table-level reachability would inflate downstream_reach, so it is never asked for."""
    change, expected = fixture_pair("01_rename")
    reader = ReplayReader(expected)
    settings = Settings(max_hops=2)
    analyze_column(change, reader, settings)  # type: ignore[arg-type]

    assert reader.lineage_calls == [(change.dataset_urn, change.column, 2)]  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Owners
# ---------------------------------------------------------------------------


def test_owners_are_deduplicated_and_attributed() -> None:
    change, expected = fixture_pair("01_rename")
    impact, _ = analyze_column(change, ReplayReader(expected), SETTINGS)  # type: ignore[arg-type]

    urns = [o.urn for o in impact.owners_to_notify]
    assert len(urns) == len(set(urns))
    for owner in impact.owners_to_notify:
        assert owner.source is not None
        assert owner.for_urn is not None


def test_the_changed_datasets_owner_comes_first() -> None:
    """Whoever owns the thing being changed is the first person the comment names."""
    change, expected = fixture_pair("01_rename")
    impact, _ = analyze_column(change, ReplayReader(expected), SETTINGS)  # type: ignore[arg-type]

    assert impact.owners_to_notify[0].source == "changed_dataset"


# ---------------------------------------------------------------------------
# Degradation: the difference between "zero" and "unmeasured".
# ---------------------------------------------------------------------------


def test_every_failed_read_becomes_a_visible_degradation() -> None:
    change, expected = fixture_pair("01_rename")
    impact, degradations = analyze_column(change, BrokenReader(expected), SETTINGS)  # type: ignore[arg-type]

    capabilities = {d.capability for d in degradations}
    assert capabilities >= {
        "column_level_lineage",
        "ownership",
        "assertions",
        "data_contracts",
        "query_usage",
    }
    # The analysis still produced a report rather than dying.
    assert impact.severity.score >= 0
    assert impact.downstream == ()


def test_unmeasured_usage_is_reported_even_when_the_read_succeeds() -> None:
    """`source="unavailable"` is a successful read that measured nothing."""
    change, expected = fixture_pair("01_rename")
    blank = expected.model_copy(
        update={
            "query_usage": QueryUsage(window_days=30, query_count=0, source="unavailable"),
        }
    )
    _, degradations = analyze_column(change, ReplayReader(blank), SETTINGS)  # type: ignore[arg-type]

    usage_degradations = [d for d in degradations if d.capability == "query_usage"]
    assert usage_degradations, "unmeasured usage must never look like measured zero"
    # The consequence now carries the lower-bound statement and the range the
    # true severity lies in, rather than a hardcoded "up to 15 points" sentence.
    consequence = usage_degradations[0].consequence or ""
    assert "LOWER BOUND" in consequence
    assert "true severity is between" in consequence


def test_a_measured_zero_is_not_a_degradation() -> None:
    change, expected = fixture_pair("01_rename")
    measured = expected.model_copy(
        update={
            "query_usage": QueryUsage(window_days=30, query_count=0, source="datahub_usage"),
        }
    )
    _, degradations = analyze_column(change, ReplayReader(measured), SETTINGS)  # type: ignore[arg-type]

    assert [d for d in degradations if d.capability == "query_usage"] == []


def test_an_empty_downstream_set_is_not_a_degradation() -> None:
    """Nothing downstream is a real answer, and a common one for a leaf table."""
    change, expected = fixture_pair("01_rename")
    isolated = expected.model_copy(update={"downstream": (), "owners_to_notify": ()})
    impact, degradations = analyze_column(change, ReplayReader(isolated), SETTINGS)  # type: ignore[arg-type]

    assert impact.downstream == ()
    assert [d for d in degradations if d.capability == "column_level_lineage"] == []


def test_table_level_reachability_is_reported_as_degraded() -> None:
    """An entity reached without a column-level edge must not read as analysed."""
    change, expected = fixture_pair("01_rename")
    widened = expected.model_copy(
        update={
            "downstream": tuple(
                e.model_copy(update={"via_column": None}) for e in expected.downstream
            )
        }
    )
    _, degradations = analyze_column(change, ReplayReader(widened), SETTINGS)  # type: ignore[arg-type]

    lineage = [d for d in degradations if d.capability == "column_level_lineage"]
    assert lineage, "table-level-only reachability must be declared"


def test_an_unimplemented_read_halts_rather_than_degrading() -> None:
    """The most important test in this file.

    A stub must never be absorbed into a degradation. If it were, `analyze`
    would emit a complete-looking report built from an empty graph, and the
    severity in it would be fiction.
    """
    change, expected = fixture_pair("01_rename")

    class StubbedReader(ReplayReader):
        def get_lineage(self, *args: object, **kwargs: object) -> tuple[DownstreamEntity, ...]:
            """Behave like a reader method OWNER A has not written yet."""
            _raise_as_an_unwritten_reader_would()
            return ()

    with pytest.raises(NotImplementedError):
        analyze_column(change, StubbedReader(expected), SETTINGS)  # type: ignore[arg-type]


def test_an_entity_a_reader_had_to_drop_reaches_the_report() -> None:
    """A partial read is a third case, and it used to have nowhere to go.

    `get_lineage` over MCP drops any entity whose route it could not
    demonstrate — an entity without a path is not reportable. Dropping it
    silently would lower the score with nothing in the report to say so, which
    is the same class of failure as returning an empty tuple for a capability
    that is missing.
    """
    change, expected = fixture_pair("01_rename")

    class DroppingReader(ReplayReader):
        """A reader that reached something it could not prove a route to."""

        def drain_notes(self) -> tuple[ReaderNote, ...]:
            return (
                ReaderNote(
                    capability="column_level_lineage",
                    reason="1 entity(ies) were dropped: urn:li:dataset:(x,y,PROD)",
                    consequence="Their absence is not a finding of 'no impact'.",
                ),
            )

    _, degradations = analyze_column(change, DroppingReader(expected), SETTINGS)  # type: ignore[arg-type]

    dropped = [d for d in degradations if "dropped" in d.reason]
    assert len(dropped) == 1
    assert dropped[0].capability == "column_level_lineage"
    assert dropped[0].consequence is not None


def test_a_reader_with_nothing_to_report_adds_no_degradation() -> None:
    """Notes are an optional capability; a reader without them is not a gap."""
    change, expected = fixture_pair("01_rename")

    _, degradations = analyze_column(change, ReplayReader(expected), SETTINGS)  # type: ignore[arg-type]

    assert not [d for d in degradations if "dropped" in d.reason]


# ---------------------------------------------------------------------------
# Whole change sets
# ---------------------------------------------------------------------------


def test_change_set_analysis_preserves_input_order() -> None:
    directory = fixture_dir("01_rename")
    change_set = load_change_set(directory / "change_set.json")
    expected = load_impact_report(directory / "expected_impact_report.json").column_impacts[0]

    impacts, _ = analyze_change_set(change_set, ReplayReader(expected), SETTINGS)  # type: ignore[arg-type]

    assert [i.change_id for i in impacts] == [c.id for c in change_set.column_changes]


def test_degradations_are_deduplicated_by_capability() -> None:
    directory = fixture_dir("01_rename")
    change_set = load_change_set(directory / "change_set.json")
    expected = load_impact_report(directory / "expected_impact_report.json").column_impacts[0]

    _, degradations = analyze_change_set(change_set, BrokenReader(expected), SETTINGS)  # type: ignore[arg-type]

    capabilities = [d.capability for d in degradations]
    assert len(capabilities) == len(set(capabilities))


def test_a_column_survives_every_single_read_failing() -> None:
    """Total DataHub failure still produces a report — one made entirely of degradations.

    This is the shape the pipeline needs: the reviewer gets a comment saying
    "nothing could be measured", not a crash and not a confident zero.
    """
    directory = fixture_dir("01_rename")
    change_set = load_change_set(directory / "change_set.json")
    expected = load_impact_report(directory / "expected_impact_report.json").column_impacts[0]

    class TotallyBroken(ReplayReader):
        def get_lineage(self, *args: object, **kwargs: object) -> tuple[DownstreamEntity, ...]:
            msg = "GMS unreachable"
            raise DataHubAccessError(msg)

        def get_owners(self, *args: object, **kwargs: object) -> tuple[Owner, ...]:
            msg = "GMS unreachable"
            raise DataHubAccessError(msg)

    reader = TotallyBroken(expected)

    def explode(*args: object, **kwargs: object) -> None:
        msg = "GMS unreachable"
        raise DataHubAccessError(msg)

    # Make the column-level entry point itself collapse.
    reader.get_assertions = explode  # type: ignore[method-assign,assignment]
    reader.get_data_contracts = explode  # type: ignore[method-assign,assignment]
    reader.get_dataset_queries = explode  # type: ignore[method-assign,assignment]

    impacts, _ = analyze_change_set(change_set, reader, SETTINGS)  # type: ignore[arg-type]
    # Every individual read degraded, but the column still produced an impact.
    assert len(impacts) == 1


# ---------------------------------------------------------------------------
# Regression: column-level coverage, through the analyzer.
# ---------------------------------------------------------------------------


def test_an_irrelevant_contract_no_longer_inflates_the_score() -> None:
    """Fixture 02 scores 96.0 with a contract that names the changed column.

    Point the same contract at a different column and the 12-point
    contract_presence factor must drop out. Before the fix it stayed, because
    `contract_covers_column` returned True for any ACTIVE or PENDING contract.
    """
    change, expected = fixture_pair("02_removal_contract")

    elsewhere = expected.model_copy(
        update={
            "data_contracts": tuple(
                c.model_copy(update={"references_changed_column": False})
                for c in expected.data_contracts
            )
        }
    )
    impact, _ = analyze_column(change, ReplayReader(elsewhere), SETTINGS)  # type: ignore[arg-type]

    contract_factor = next(f for f in impact.severity.factors if f.name == "contract_presence")
    assert contract_factor.contribution == 0.0
    assert impact.severity.score == expected.severity.score - 12.0


def test_an_irrelevant_assertion_no_longer_inflates_the_score() -> None:
    """Same for the 4-point assertion factor, which was `len(assertions) > 0`."""
    change, expected = fixture_pair("03_adversarial_description")

    elsewhere = expected.model_copy(
        update={
            "assertions": tuple(
                a.model_copy(update={"references_changed_column": False})
                for a in expected.assertions
            )
        }
    )
    impact, _ = analyze_column(change, ReplayReader(elsewhere), SETTINGS)  # type: ignore[arg-type]

    assertion_factor = next(f for f in impact.severity.factors if f.name == "assertion_presence")
    assert assertion_factor.contribution == 0.0
    assert impact.severity.score == expected.severity.score - 4.0


def test_unreadable_coverage_is_reported_rather_than_assumed() -> None:
    """`None` scores as not-covering, and says so, instead of counting silently."""
    change, expected = fixture_pair("02_removal_contract")

    unknown = expected.model_copy(
        update={
            "data_contracts": tuple(
                c.model_copy(update={"references_changed_column": None})
                for c in expected.data_contracts
            ),
            "assertions": tuple(
                a.model_copy(update={"references_changed_column": None})
                for a in expected.assertions
            ),
        }
    )
    impact, degradations = analyze_column(change, ReplayReader(unknown), SETTINGS)  # type: ignore[arg-type]

    assert impact.severity.score == expected.severity.score - 16.0
    coverage = [d for d in degradations if "coverage could not be determined" in d.reason]
    assert coverage, "an unread coverage flag must be visible in the report"


def test_the_adversarial_fixture_still_scores_seventy_seven() -> None:
    """The headline number. Its assertion genuinely names the removed column."""
    change, expected = fixture_pair("03_adversarial_description")
    impact, _ = analyze_column(change, ReplayReader(expected), SETTINGS)  # type: ignore[arg-type]

    assert impact.severity.score == 77.0
    assert impact.severity.level == "critical"
