"""The SDK access path, exercised against a fake graph client.

Both reads covered here failed the same way against a live DataHub: they
returned a value that scored as a measurement and was not one. That is the
failure this repository is built to prevent, so each has a test that fails if
the read ever goes back to being unable to tell absence from refusal.

No DataHub and no Docker: `SdkDataHubReader` builds its `DataHubGraph` lazily
through a cached property, so a fake can be installed in its place.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from core.datahub.sdk_client import SdkDataHubReader
from core.errors import DataHubAccessError

DATASET = "urn:li:dataset:(urn:li:dataPlatform:duckdb,blast_radius_demo.main.dim_customers,PROD)"
ASSERTION = "urn:li:assertion:8b2c1f0e"
CONTRACT = "urn:li:dataContract:c0ffee"


class FakeResponse:
    """The subset of `requests.Response` this read touches."""

    def __init__(self, status_code: int, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        if self._payload is None:
            msg = "no JSON object could be decoded"
            raise ValueError(msg)
        return self._payload


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.posts: list[tuple[str, dict[str, Any]]] = []

    def post(self, url: str, data: str, **kwargs: Any) -> FakeResponse:
        self.posts.append((url, json.loads(data)))
        return self.response


class FakeConfig:
    server = "http://gms:8080"


class FakeGraph:
    """Stands in for `DataHubGraph`."""

    def __init__(
        self,
        response: FakeResponse | None = None,
        aspects: dict[tuple[str, str], Any] | None = None,
        related: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        self.config = FakeConfig()
        self._session = FakeSession(response or FakeResponse(200, {"value": {"values": []}}))
        self._aspects = aspects or {}
        self._related = related or {}

    def get_aspect(self, entity_urn: str, aspect_type: Any) -> Any:
        return self._aspects.get((entity_urn, aspect_type.__name__))

    def get_related_entities(self, urn: str, relationships: list[str], direction: Any) -> Any:
        class _Related:
            def __init__(self, urn: str) -> None:
                self.urn = urn

        return [_Related(u) for u in self._related.get(relationships[0], ())]


def reader_with(graph: FakeGraph) -> SdkDataHubReader:
    client = SdkDataHubReader("http://gms:8080")
    client._graph_cache = graph  # type: ignore[assignment]
    return client


def usage_payload(*buckets: dict[str, Any]) -> dict[str, Any]:
    """A `getTimeseriesAspectValues` response carrying usage aspects."""
    return {
        "value": {
            "values": [{"aspect": {"value": json.dumps(bucket)}} for bucket in buckets],
        }
    }


# -- finding 5: usage that could not tell absence from refusal ----------------


def test_the_usage_request_carries_a_limit() -> None:
    """GMS NPEs without it, and the SDK helper that omits it swallows the 500.

        java.lang.NullPointerException: Cannot invoke "java.lang.Integer.intValue()"
        because "limit" is null

    The helper logged that at DEBUG and returned None, which is the same value
    it returns for a dataset nobody has ever queried.
    """
    graph = FakeGraph(FakeResponse(200, usage_payload()))
    reader_with(graph).get_dataset_queries(DATASET, window_days=30)

    url, body = graph._session.posts[0]
    assert url.endswith("/aspects?action=getTimeseriesAspectValues")
    assert body["limit"] > 0, "the field whose absence makes GMS answer 500"
    assert body["aspect"] == "datasetUsageStatistics"
    assert body["urn"] == DATASET


def test_a_rejected_usage_request_raises_rather_than_reading_as_absence() -> None:
    """A 500 is not evidence that nobody queries the table.

    This is the distinction the whole report rests on: `query_usage` scoring
    zero is only defensible if a zero means "measured, and it was zero".
    """
    graph = FakeGraph(FakeResponse(500))

    with pytest.raises(DataHubAccessError, match="rejected request"):
        reader_with(graph).get_dataset_queries(DATASET)


def test_usage_sums_every_bucket_in_the_window() -> None:
    """The live catalog held 30 daily buckets summing to 1450 queries."""
    graph = FakeGraph(
        FakeResponse(
            200,
            usage_payload(
                {"totalSqlQueries": 1000, "uniqueUserCount": 4, "timestampMillis": 1_700_000_000},
                {"totalSqlQueries": 450, "uniqueUserCount": 3, "timestampMillis": 1_600_000_000},
            ),
        )
    )

    usage = reader_with(graph).get_dataset_queries(DATASET, window_days=30)

    assert usage.query_count == 1450
    assert usage.source == "datahub_usage"
    assert usage.distinct_user_count == 4


def test_a_genuinely_empty_window_is_still_unavailable_not_zero() -> None:
    """The old distinction has to survive the fix that made it meaningful."""
    usage = reader_with(
        FakeGraph(FakeResponse(200, {"value": {"values": []}}))
    ).get_dataset_queries(DATASET)

    assert usage.source == "unavailable"
    assert usage.query_count == 0


# -- finding 6: the contract -> assertion -> field hop ------------------------


class FakeAssertionInfo:
    """`AssertionInfoClass` shaped like a field assertion on one column.

    The camelCase attribute names are DataHub's, not a style slip: `_asserted_fields`
    reads them off the real aspect class by those exact names, so a fake that
    renamed them would pass while testing nothing.
    """

    def __init__(self, field_path: str) -> None:
        self.type = "FIELD"
        self.description = None
        self.datasetAssertion = None

        class _Field:
            path = field_path
            urn = f"urn:li:schemaField:({DATASET},{field_path})"

        class _Values:
            field = _Field()

        class _FieldAssertion:
            fieldValuesAssertion = _Values()  # noqa: N815
            fieldMetricAssertion = None  # noqa: N815

        self.fieldAssertion = _FieldAssertion()


class FakeContractProperties:
    """`DataContractPropertiesClass` carries assertion URNs, never field paths."""

    def __init__(self, assertion_urns: tuple[str, ...]) -> None:
        class _Entry:
            def __init__(self, assertion: str) -> None:
                self.assertion = assertion

        self.schema = [_Entry(urn) for urn in assertion_urns]
        self.dataQuality: list[_Entry] = []
        self.freshness: list[_Entry] = []


class FakeContractStatus:
    state = "ACTIVE"


def contract_graph(info: Any, assertion_urn: str = ASSERTION) -> FakeGraph:
    from datahub.metadata.schema_classes import (
        AssertionInfoClass,
        DataContractPropertiesClass,
        DataContractStatusClass,
    )

    aspects: dict[tuple[str, str], Any] = {
        (CONTRACT, DataContractPropertiesClass.__name__): FakeContractProperties((assertion_urn,)),
        (CONTRACT, DataContractStatusClass.__name__): FakeContractStatus(),
    }
    if info is not None:
        aspects[(assertion_urn, AssertionInfoClass.__name__)] = info
    return FakeGraph(aspects=aspects, related={"ContractFor": (CONTRACT,)})


def test_a_contract_resolves_through_its_assertion_to_a_column() -> None:
    """`contract_presence` is 12 points and could never fire before this.

    `DataContractPropertiesClass` carries assertion URNs. Handing those to
    `references_column`, which matches schemaField URNs and bare field paths,
    matched nothing — so every contract that ever existed reported False.
    """
    graph = contract_graph(FakeAssertionInfo("signup_channel"))

    contracts = reader_with(graph).get_data_contracts(DATASET, "signup_channel")

    assert len(contracts) == 1
    assert contracts[0].references_changed_column is True


def test_a_contract_on_another_column_is_a_measured_no() -> None:
    """Coverage of a different column is a real answer, and it is False."""
    graph = contract_graph(FakeAssertionInfo("unrelated_column"))

    contracts = reader_with(graph).get_data_contracts(DATASET, "signup_channel")

    assert contracts[0].references_changed_column is False


def test_an_unreadable_assertion_makes_coverage_unknown_not_false() -> None:
    """Unmeasured must not look measured, or `unknown_coverage` never fires.

    False scores the same as None but says something the reader did not learn,
    and suppresses the degradation that would have disclosed the gap.
    """
    graph = contract_graph(None)

    contracts = reader_with(graph).get_data_contracts(DATASET, "signup_channel")

    assert contracts[0].references_changed_column is None


def test_a_contract_naming_no_assertions_is_unknown() -> None:
    """A contract with no resolvable terms governs nothing we can check."""
    from datahub.metadata.schema_classes import (
        DataContractPropertiesClass,
        DataContractStatusClass,
    )

    graph = FakeGraph(
        aspects={
            (CONTRACT, DataContractPropertiesClass.__name__): FakeContractProperties(()),
            (CONTRACT, DataContractStatusClass.__name__): FakeContractStatus(),
        },
        related={"ContractFor": (CONTRACT,)},
    )

    contracts = reader_with(graph).get_data_contracts(DATASET, "signup_channel")

    assert contracts[0].references_changed_column is None
