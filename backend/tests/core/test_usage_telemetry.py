"""Usage telemetry (ADR 0050): closed vocabulary, two gates, the file sink, the OTLP wiring."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from backend.app.core import otel, usage_telemetry
from backend.app.core.config import Settings, get_settings
from backend.app.core.usage_telemetry import (
    CLIENT_EVENTS,
    COUNT,
    EVENTS,
    UsageEvent,
    UsageEventInvalidError,
)
from backend.app.db.models import User
from backend.app.services import telemetry_settings_service as svc

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    usage_telemetry.reset()
    svc.invalidate()
    usage_telemetry.register_mcp_tool_names(["list_suites"])
    yield
    usage_telemetry.reset()
    svc.invalidate()


@pytest.fixture
def admin(db_session: Any) -> User:
    user = User(id=uuid.uuid4(), aad_object_id=None, email="ada@example.com", role="admin")
    db_session.add(user)
    db_session.commit()
    return user


def _capture() -> tuple[Any, Any]:
    from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    logs, metrics = InMemoryLogRecordExporter(), InMemoryMetricReader()
    assert usage_telemetry.configure("test", log_exporters=[logs], metric_readers=[metrics])
    return logs, metrics


def _counter_points(reader: Any) -> list[tuple[dict[str, Any], int]]:
    data = reader.get_metrics_data()
    if data is None:
        return []
    return [
        (dict(point.attributes), point.value)
        for rm in data.resource_metrics
        for sm in rm.scope_metrics
        for metric in sm.metrics
        if metric.name == usage_telemetry.COUNTER_NAME
        for point in metric.data.data_points
    ]


# ───────────────────────── vocabulary ─────────────────────────


def test_every_event_is_declared_with_closed_attributes() -> None:
    assert set(EVENTS) == set(UsageEvent)
    for event, spec in EVENTS.items():
        assert spec, event
        for key, vocabulary in spec.items():
            described = vocabulary.describe()
            assert described == "integer" or (isinstance(described, list) and described), (
                event,
                key,
            )


@pytest.mark.parametrize("event", list(UsageEvent))
def test_no_string_attribute_can_carry_arbitrary_text(event: UsageEvent) -> None:
    """The guarantee the ADR makes: a table name, SQL, an email or free text is refused on
    EVERY string attribute of EVERY event, and an undeclared attribute is refused too."""
    usage_telemetry.register_mcp_tool_names(["list_suites"])
    for key, vocabulary in EVENTS[event].items():
        if vocabulary is COUNT:
            for bad in ("12", -1, True, 1.5):
                with pytest.raises(UsageEventInvalidError):
                    usage_telemetry.validate(event, {key: bad})
            continue
        for text in ("orders", "SELECT * FROM t", "ada@example.com", "", "Admin"):
            with pytest.raises(UsageEventInvalidError) as exc:
                usage_telemetry.validate(event, {key: text})
            assert text not in str(exc.value) or text == ""
    with pytest.raises(UsageEventInvalidError):
        usage_telemetry.validate(event, {"table": "orders"})


def test_validate_accepts_each_declared_value() -> None:
    usage_telemetry.register_mcp_tool_names(["list_suites"])
    for event, spec in EVENTS.items():
        attributes = {
            key: (3 if vocabulary.describe() == "integer" else vocabulary.describe()[0])
            for key, vocabulary in spec.items()
        }
        assert usage_telemetry.validate(event, attributes) == attributes


def test_unknown_event_and_unregistered_mcp_tool_are_refused() -> None:
    with pytest.raises(UsageEventInvalidError):
        usage_telemetry.validate("table_scanned", {})
    usage_telemetry.register_mcp_tool_names([])
    with pytest.raises(UsageEventInvalidError):
        usage_telemetry.validate(UsageEvent.MCP_TOOL_CALLED, {"tool": "list_suites"})


def test_client_events_are_the_two_browser_ones() -> None:
    assert CLIENT_EVENTS == {UsageEvent.PAGE_VIEWED, UsageEvent.CHECK_EDITOR_ABANDONED}


@pytest.mark.parametrize(
    ("marker", "kind"),
    [
        ("manual:1234", "manual"),
        ("schedule:abcd", "schedule"),
        ("adf:pipeline:run-9", "adf"),
        ("mcp:1", "mcp"),
        ("probe:1", "probe"),
        ("something-else", "other"),
        ("", "other"),
    ],
)
def test_trigger_kind_never_leaks_the_marker(marker: str, kind: str) -> None:
    assert usage_telemetry.trigger_kind(marker) == kind


def test_describe_events_is_the_auditable_list() -> None:
    described = {e["name"]: e["attributes"] for e in usage_telemetry.describe_events()}
    assert set(described) == {e.value for e in UsageEvent}
    assert described["page_viewed"]["route"] == sorted(usage_telemetry.ROUTE_NAMES)
    assert described["run_completed"]["checks"] == "integer"


# ───────────────────────── the two gates ─────────────────────────


def test_nothing_is_emitted_while_no_sink_is_configured(db_session: Any, admin: User) -> None:
    svc.set_enabled(db_session, enabled=True, actor=admin)
    assert svc.stored_enabled(db_session) is True
    assert usage_telemetry.emit(UsageEvent.APP_STARTED, session=db_session, service="api") is False


def test_nothing_is_emitted_while_the_admin_has_not_opted_in(db_session: Any) -> None:
    logs, metrics = _capture()
    assert usage_telemetry.emit(UsageEvent.APP_STARTED, session=db_session, service="api") is False
    assert logs.get_finished_logs() == ()
    assert _counter_points(metrics) == []


def test_both_gates_on_records_a_counter_and_a_log_event(db_session: Any, admin: User) -> None:
    logs, metrics = _capture()
    svc.set_enabled(db_session, enabled=True, actor=admin)

    assert usage_telemetry.emit(
        UsageEvent.CHECK_CREATED,
        session=db_session,
        kind="freshness",
        engine="gx",
        datasource_type="postgres",
        origin="user",
    )

    (record,) = logs.get_finished_logs()
    assert record.log_record.event_name == "dataq.usage.check_created"
    assert dict(record.log_record.attributes) == {
        "event": "check_created",
        "kind": "freshness",
        "engine": "gx",
        "datasource_type": "postgres",
        "origin": "user",
    }
    assert _counter_points(metrics) == [(dict(record.log_record.attributes), 1)]


def test_an_invalid_value_is_dropped_not_exported(db_session: Any, admin: User) -> None:
    logs, _ = _capture()
    svc.set_enabled(db_session, enabled=True, actor=admin)
    assert (
        usage_telemetry.emit(UsageEvent.PAGE_VIEWED, session=db_session, route="/suites/42")
        is False
    )
    assert logs.get_finished_logs() == ()


def test_the_opt_in_toggle_lands_without_a_restart(db_session: Any, admin: User) -> None:
    logs, _ = _capture()
    assert svc.opt_in(db_session) is False  # cached off
    svc.set_enabled(db_session, enabled=True, actor=admin)
    assert usage_telemetry.emit(UsageEvent.APP_STARTED, session=db_session, service="api")
    svc.set_enabled(db_session, enabled=False, actor=admin)
    assert usage_telemetry.emit(UsageEvent.APP_STARTED, session=db_session, service="api") is False
    assert len(logs.get_finished_logs()) == 1


def test_an_unreadable_opt_in_reads_as_off(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(_session: Any) -> bool:
        raise RuntimeError("db down")

    monkeypatch.setattr(svc, "stored_enabled", _boom)
    assert svc.opt_in(object()) is False  # type: ignore[arg-type]


def test_emit_never_raises(db_session: Any, admin: User, monkeypatch: pytest.MonkeyPatch) -> None:
    _capture()
    svc.set_enabled(db_session, enabled=True, actor=admin)
    state = usage_telemetry._state
    assert state is not None

    class _Broken:
        def add(self, *_: Any, **__: Any) -> None:
            raise RuntimeError("exporter gone")

    monkeypatch.setattr(state, "counter", _Broken())
    assert usage_telemetry.emit(UsageEvent.APP_STARTED, session=db_session, service="api") is False


# ───────────────────────── sinks ─────────────────────────


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def test_file_sink_round_trip(tmp_path: Path, db_session: Any, admin: User) -> None:
    path = tmp_path / "usage" / "events.jsonl"
    settings = _settings(telemetry_sink="file", telemetry_file_path=str(path))
    assert usage_telemetry.configure("dataq-api", settings)
    svc.set_enabled(db_session, enabled=True, actor=admin)

    assert usage_telemetry.emit(UsageEvent.CONNECTION_CREATED, session=db_session, type="s3")
    assert usage_telemetry.emit(UsageEvent.CONNECTION_CREATED, session=db_session, type="s3")
    usage_telemetry.flush()

    lines = [json.loads(line) for line in path.read_text().splitlines()]
    events = [line for line in lines if line["type"] == "event"]
    metrics = [line for line in lines if line["type"] == "metric"]
    assert [e["event_name"] for e in events] == ["dataq.usage.connection_created"] * 2
    assert events[0]["attributes"] == {"event": "connection_created", "type": "s3"}
    assert events[0]["resource"]["service.name"] == "dataq-api"
    assert events[0]["time_unix_nano"] > 0
    assert metrics == [
        {
            **metrics[0],
            "name": usage_telemetry.COUNTER_NAME,
            "attributes": {"event": "connection_created", "type": "s3"},
            "value": 2,
        }
    ]


def test_file_sink_that_cannot_be_written_leaves_telemetry_off(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("")
    settings = _settings(telemetry_sink="file", telemetry_file_path=str(blocker / "x.jsonl"))
    assert usage_telemetry.configure("dataq-api", settings) is False
    assert usage_telemetry.configured() is False


def test_otlp_sink_wires_the_metric_exporter_to_the_metrics_path() -> None:
    settings = _settings(
        telemetry_sink="otlp", otel_exporter_otlp_endpoint="http://collector:4318/"
    )
    (exporter,) = otel.build_metric_exporters(settings)
    assert type(exporter).__name__ == "OTLPMetricExporter"
    assert exporter._endpoint == "http://collector:4318/v1/metrics"
    assert usage_telemetry.configure("dataq-api", settings)
    state = usage_telemetry._state
    assert state is not None
    readers = state.meter_provider._all_metric_readers
    assert [type(r._exporter).__name__ for r in readers] == ["OTLPMetricExporter"]


def test_off_sink_configures_nothing() -> None:
    assert usage_telemetry.configure("dataq-api", _settings()) is False
    assert usage_telemetry.configured() is False


def test_settings_refuse_a_sink_that_cannot_deliver() -> None:
    with pytest.raises(ValidationError, match="TELEMETRY_FILE_PATH"):
        _settings(telemetry_sink="file")
    with pytest.raises(ValidationError, match="OTEL_EXPORTER_OTLP_ENDPOINT"):
        _settings(telemetry_sink="otlp")
    assert get_settings().telemetry_sink == "off"
