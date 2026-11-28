"""`kind_detail` reads the keys the monitor producers actually write (#1740).

The evidence card lifts monitor-kind fields out of `observed_value` by name, and each producer
spells those names on its own. These tests run every producer for real and pass its output
through `_kind_detail_layer`, so a rename on either side fails here instead of degrading the card
to nulls. A key the layer reads with a default (`.get(key, default)`) may be absent by design: a
baseline run has no diff yet, a short history has no z-score.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.datasources import monitors
from backend.app.services import anomaly, schema_drift
from backend.app.services.incident_evidence import MONITOR_KINDS, _kind_detail_layer

NOW = datetime(2026, 11, 28, 12, tzinfo=UTC)


class Reads(dict[str, Any]):
    """An `observed_value` that records which keys the layer reads, and how."""

    def __init__(self, data: dict[str, Any]) -> None:
        super().__init__(data)
        self.required: set[str] = set()

    def get(self, key: str, *default: Any) -> Any:
        if not default:
            self.required.add(key)
        return super().get(key, *default)


def assert_layer_reads_what(kind: str, observed: dict[str, Any] | None) -> None:
    assert isinstance(observed, dict), f"{kind}: the producer wrote no observed_value"
    reads = Reads(observed)
    detail = _kind_detail_layer(
        SimpleNamespace(kind=kind),  # type: ignore[arg-type]
        SimpleNamespace(observed_value=reads),  # type: ignore[arg-type]
    )

    assert detail is not None, f"{kind}: no kind_detail for a monitor-kind result"
    missing = reads.required - observed.keys()
    assert not missing, f"{kind}: kind_detail reads {sorted(missing)}, the producer never writes"


def test_freshness() -> None:
    out = monitors.monitor_outcome(
        monitors.FRESHNESS, scalar=NOW - timedelta(hours=3), config={"column": "ts"}, now=NOW
    )
    assert_layer_reads_what(monitors.FRESHNESS, out.observed_value)


def test_volume() -> None:
    out = monitors.monitor_outcome(monitors.VOLUME, scalar=5, config={"min_rows": 10}, now=NOW)
    assert_layer_reads_what(monitors.VOLUME, out.observed_value)


def test_schema_drift_once_compared() -> None:
    payload = schema_drift.diff_schemas(
        [{"name": "id", "type": "int"}, {"name": "gone", "type": "text"}],
        [{"name": "id", "type": "bigint"}, {"name": "new", "type": "text"}],
    )
    out = monitors.monitor_outcome(monitors.SCHEMA_DRIFT, scalar=payload, config={}, now=NOW)
    assert_layer_reads_what(monitors.SCHEMA_DRIFT, out.observed_value)


def test_anomaly_with_enough_history() -> None:
    params = monitors.anomaly_params({"target_metric": "row_count", "min_points": 3})
    payload = anomaly.build_score_payload(50.0, [10.0, 11.0, 12.0, 10.5], params)
    assert_layer_reads_what(monitors.ANOMALY, payload)


def test_aggregate_with_a_breached_bound() -> None:
    config = {"aggregate": "mean", "column": "amount", "max_value": 10}
    out = monitors.monitor_outcome(monitors.AGGREGATE, scalar=25, config=config, now=NOW)
    assert_layer_reads_what(monitors.AGGREGATE, out.observed_value)


def test_every_monitor_kind_is_covered() -> None:
    """pii_drift's producer needs a profiled database: its contract test is in test_pii_drift."""
    covered = {
        monitors.FRESHNESS,
        monitors.VOLUME,
        monitors.SCHEMA_DRIFT,
        monitors.ANOMALY,
        monitors.AGGREGATE,
        monitors.PII_DRIFT,
    }
    assert covered == MONITOR_KINDS, "a new monitor kind needs a producer contract test here"


@pytest.mark.parametrize("kind", ["expectation", "comparison"])
def test_non_monitor_kinds_have_no_detail(kind: str) -> None:
    check, result = SimpleNamespace(kind=kind), SimpleNamespace(observed_value={})
    assert _kind_detail_layer(check, result) is None  # type: ignore[arg-type]
