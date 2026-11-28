"""`kind_detail` reads the keys the monitor producers actually write (#1740).

The evidence card lifts monitor-kind fields out of `observed_value` by name, and each producer
spells those names on its own. These tests run every producer for real and pass its output
through `_kind_detail_layer`, so a rename on either side fails here instead of degrading the card
to nulls or to an empty list that reads as "nothing changed". Each case is a branch where the
producer must write every key the layer reads, apart from the ones named as absent by design
there (a compared schema-drift run has no `baseline_captured`, a scored anomaly no
`insufficient_history`).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.datasources import monitors
from backend.app.services import anomaly, schema_drift
from backend.app.services.incident_evidence import MONITOR_KINDS, _kind_detail_layer

NOW = datetime(2026, 11, 28, 12, tzinfo=UTC)


class Reads(dict[str, Any]):
    """An `observed_value` that records every key the layer reads, however it reads it."""

    def __init__(self, data: dict[str, Any]) -> None:
        super().__init__(data)
        self.read: set[str] = set()

    def get(self, key: str, *default: Any) -> Any:
        self.read.add(key)
        return super().get(key, *default)

    def __getitem__(self, key: str) -> Any:
        self.read.add(key)
        return super().__getitem__(key)

    def __contains__(self, key: object) -> bool:
        if isinstance(key, str):
            self.read.add(key)
        return super().__contains__(key)


def assert_layer_reads_what(
    kind: str, observed: dict[str, Any] | None, *, absent_by_design: frozenset[str] = frozenset()
) -> None:
    assert isinstance(observed, dict), f"{kind}: the producer wrote no observed_value"
    reads = Reads(observed)
    detail = _kind_detail_layer(
        SimpleNamespace(kind=kind),  # type: ignore[arg-type]
        SimpleNamespace(observed_value=reads),  # type: ignore[arg-type]
    )

    assert detail is not None, f"{kind}: no kind_detail for a monitor-kind result"
    assert reads.read, f"{kind}: the layer read no key, so this test checks nothing"
    missing = reads.read - observed.keys() - absent_by_design
    assert not missing, f"{kind}: kind_detail reads {sorted(missing)}, the producer never writes"


def _freshness() -> None:
    out = monitors.monitor_outcome(
        monitors.FRESHNESS, scalar=NOW - timedelta(hours=3), config={"column": "ts"}, now=NOW
    )
    assert_layer_reads_what(monitors.FRESHNESS, out.observed_value)


def _volume() -> None:
    out = monitors.monitor_outcome(monitors.VOLUME, scalar=5, config={"min_rows": 10}, now=NOW)
    assert_layer_reads_what(monitors.VOLUME, out.observed_value)


def _schema_drift() -> None:
    payload = schema_drift.diff_schemas(
        [{"name": "id", "type": "int"}, {"name": "gone", "type": "text"}],
        [{"name": "id", "type": "bigint"}, {"name": "new", "type": "text"}],
    )
    out = monitors.monitor_outcome(monitors.SCHEMA_DRIFT, scalar=payload, config={}, now=NOW)
    assert_layer_reads_what(
        monitors.SCHEMA_DRIFT, out.observed_value, absent_by_design=frozenset({"baseline_captured"})
    )


def _anomaly() -> None:
    params = monitors.anomaly_params({"target_metric": "row_count", "min_points": 3})
    payload = anomaly.build_score_payload(50.0, [10.0, 11.0, 12.0, 10.5], params)
    assert_layer_reads_what(
        monitors.ANOMALY, payload, absent_by_design=frozenset({"insufficient_history"})
    )
    # The column-profile scorer writes another shape (#2795).
    key = anomaly.series_key("email", anomaly.NULL_PCT)
    profile = anomaly.ColumnProfile(
        row_count=100, series={key: 40.0}, columns_measured=1, columns_total=1
    )
    history = [(NOW - timedelta(days=4 - i), {key: v}) for i, v in enumerate([1.0, 1.2, 0.8, 1.0])]
    profile_params = monitors.anomaly_params({"target_metric": "column_profile", "min_points": 3})
    scored = anomaly.score_column_profile(profile, history, now=NOW, params=profile_params)
    assert_layer_reads_what(
        monitors.ANOMALY, scored, absent_by_design=frozenset({"insufficient_history"})
    )
    # The first runs, before any series has enough history.
    cold = anomaly.score_column_profile(profile, history[:1], now=NOW, params=profile_params)
    assert_layer_reads_what(
        monitors.ANOMALY, cold, absent_by_design=frozenset({"z_score", "deviations"})
    )
    detail = _kind_detail_layer(
        SimpleNamespace(kind=monitors.ANOMALY),  # type: ignore[arg-type]
        SimpleNamespace(observed_value=cold),  # type: ignore[arg-type]
    )
    assert detail is not None and detail["insufficient_history"] is True


def _aggregate() -> None:
    config = {"aggregate": "mean", "column": "amount", "max_value": 10}
    out = monitors.monitor_outcome(monitors.AGGREGATE, scalar=25, config=config, now=NOW)
    assert_layer_reads_what(monitors.AGGREGATE, out.observed_value)


#: One producer case per monitor kind. pii_drift's producer needs a profiled database, so its
#: case lives in `test_pii_drift.py::test_kind_detail_reads_the_keys_this_executor_writes`.
PRODUCERS: dict[str, Callable[[], None] | None] = {
    monitors.FRESHNESS: _freshness,
    monitors.VOLUME: _volume,
    monitors.SCHEMA_DRIFT: _schema_drift,
    monitors.ANOMALY: _anomaly,
    monitors.AGGREGATE: _aggregate,
    monitors.PII_DRIFT: None,
}


@pytest.mark.parametrize("kind", [k for k, case in PRODUCERS.items() if case is not None])
def test_kind_detail_reads_what_the_producer_writes(kind: str) -> None:
    case = PRODUCERS[kind]
    assert case is not None
    case()


def test_every_monitor_kind_has_a_producer_case() -> None:
    assert set(PRODUCERS) == set(MONITOR_KINDS), "a new monitor kind needs a producer case here"


def test_a_renamed_defaulted_key_is_caught() -> None:
    """`added` is read with a default; a compared run that lacks it must still fail."""
    payload = schema_drift.diff_schemas([{"name": "a", "type": "int"}], [])
    payload["columns_added"] = payload.pop("added")
    out = monitors.monitor_outcome(monitors.SCHEMA_DRIFT, scalar=payload, config={}, now=NOW)
    with pytest.raises(AssertionError, match="added"):
        assert_layer_reads_what(
            monitors.SCHEMA_DRIFT,
            out.observed_value,
            absent_by_design=frozenset({"baseline_captured"}),
        )


@pytest.mark.parametrize("kind", ["expectation", "comparison"])
def test_non_monitor_kinds_have_no_detail(kind: str) -> None:
    check, result = SimpleNamespace(kind=kind), SimpleNamespace(observed_value={})
    assert _kind_detail_layer(check, result) is None  # type: ignore[arg-type]
