"""Product-usage telemetry (ADR 0050, #1707): a closed vocabulary of events emitted as
OpenTelemetry signals — one counter and one log event per occurrence — through the same
exporter seam as spans and logs (ADR 0010), or to a JSONL file when no collector exists.

Off unless BOTH gates are on: the operator's `TELEMETRY_SINK` (this module) and the admin's
stored opt-in (`telemetry_settings_service`). An attribute value is accepted only from its
event's allowlist, so no table name, SQL, suite name, email or free text can ride an event.
"""

from __future__ import annotations

import enum
import json
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from backend.app.core import otel
from backend.app.core.config import Settings, get_settings
from backend.app.core.logging import get_logger
from backend.app.db.models import (
    CHECK_ENGINES,
    CHECK_KINDS,
    CHECK_ORIGINS,
    CONNECTION_TYPES,
    LLM_INVOCATION_KINDS,
    ORCHESTRATION_PROVIDERS,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = get_logger(__name__)

METER_NAME: Final = "dataq.usage"
COUNTER_NAME: Final = "dataq.usage.events"
EVENT_NAME_PREFIX: Final = "dataq.usage."


class UsageEvent(enum.StrEnum):
    APP_STARTED = "app_started"
    USER_SIGNED_IN = "user_signed_in"
    PAGE_VIEWED = "page_viewed"
    CHECK_CREATED = "check_created"
    CHECK_EDITOR_ABANDONED = "check_editor_abandoned"
    SUITE_RUN_TRIGGERED = "suite_run_triggered"
    RUN_COMPLETED = "run_completed"
    CONNECTION_CREATED = "connection_created"
    MCP_TOOL_CALLED = "mcp_tool_called"
    LLM_FEATURE_USED = "llm_feature_used"
    ALERT_DELIVERED = "alert_delivered"
    COVERAGE_AUTO_CHECK_CREATED = "coverage_auto_check_created"


class Vocabulary:
    """What one attribute may carry: a closed set of strings, or a non-negative integer."""

    def __init__(self, values: Sequence[str] | None = None, *, integer: bool = False) -> None:
        self._values = frozenset(values or ())
        self._integer = integer

    def allows(self, value: Any) -> bool:
        if self._integer:
            return isinstance(value, int) and not isinstance(value, bool) and value >= 0
        return isinstance(value, str) and value in self._values

    def describe(self) -> list[str] | str:
        return "integer" if self._integer else sorted(self._values)


class _McpToolNames(Vocabulary):
    """The MCP server's registered tool names, declared once the server is built."""

    def __init__(self) -> None:
        super().__init__()
        self.names: frozenset[str] = frozenset()

    def allows(self, value: Any) -> bool:
        return isinstance(value, str) and value in self.names

    def describe(self) -> list[str] | str:
        return sorted(self.names)


ROUTE_NAMES: Final[tuple[str, ...]] = (
    "dashboard",
    "connections",
    "connection_new",
    "connection_edit",
    "suites",
    "suite_new",
    "suite_detail",
    "suite_edit",
    "check_new",
    "check_edit",
    "assets",
    "asset_detail",
    "results",
    "run_detail",
    "profile",
    "admin_overview",
    "admin_members",
    "admin_suites",
    "admin_settings",
    "admin_compliance",
    "admin_integrations",
    "not_found",
    "other",
)
DATASOURCE_TYPES: Final[tuple[str, ...]] = tuple(
    t for t in CONNECTION_TYPES if t not in ORCHESTRATION_PROVIDERS
)
TRIGGER_KINDS: Final[tuple[str, ...]] = (
    "manual",
    "schedule",
    "mcp",
    "probe",
    "other",
    *ORCHESTRATION_PROVIDERS,
)
RUN_OUTCOMES: Final[tuple[str, ...]] = ("succeeded", "failed", "cancelled", "other")
ALERT_CHANNELS: Final[tuple[str, ...]] = ("teams", "slack", "email", "webhook")
LLM_FEATURES: Final[tuple[str, ...]] = tuple(k for k in LLM_INVOCATION_KINDS if k != "ping")

COUNT = Vocabulary(integer=True)
MCP_TOOL_NAMES = _McpToolNames()

#: The whole vocabulary. An event not listed, an attribute not listed for its event, or a value
#: outside its vocabulary is rejected — the registry is the only way a value reaches a sink.
EVENTS: Final[dict[UsageEvent, dict[str, Vocabulary]]] = {
    UsageEvent.APP_STARTED: {"service": Vocabulary(("api", "worker"))},
    UsageEvent.USER_SIGNED_IN: {"auth_mode": Vocabulary(("pat", "session", "oidc", "bypass"))},
    UsageEvent.PAGE_VIEWED: {"route": Vocabulary(ROUTE_NAMES)},
    UsageEvent.CHECK_CREATED: {
        "kind": Vocabulary(CHECK_KINDS),
        "engine": Vocabulary(CHECK_ENGINES),
        "datasource_type": Vocabulary(DATASOURCE_TYPES),
        "origin": Vocabulary((*CHECK_ORIGINS, "import")),
    },
    UsageEvent.CHECK_EDITOR_ABANDONED: {"step": Vocabulary(("expectation", "config"))},
    UsageEvent.SUITE_RUN_TRIGGERED: {"trigger": Vocabulary(TRIGGER_KINDS)},
    UsageEvent.RUN_COMPLETED: {
        "outcome": Vocabulary(RUN_OUTCOMES),
        "datasource_type": Vocabulary(DATASOURCE_TYPES),
        "checks": COUNT,
        "duration_ms": COUNT,
    },
    UsageEvent.CONNECTION_CREATED: {"type": Vocabulary(CONNECTION_TYPES)},
    UsageEvent.MCP_TOOL_CALLED: {
        "tool": MCP_TOOL_NAMES,
        "outcome": Vocabulary(("ok", "error", "cancelled")),
    },
    UsageEvent.LLM_FEATURE_USED: {"feature": Vocabulary(LLM_FEATURES)},
    UsageEvent.ALERT_DELIVERED: {"channel": Vocabulary(ALERT_CHANNELS)},
    UsageEvent.COVERAGE_AUTO_CHECK_CREATED: {"kind": Vocabulary(CHECK_KINDS)},
}

#: The events a browser may report through `POST /telemetry/events`; the server emits the rest.
CLIENT_EVENTS: Final[frozenset[UsageEvent]] = frozenset(
    {UsageEvent.PAGE_VIEWED, UsageEvent.CHECK_EDITOR_ABANDONED}
)


class UsageEventInvalidError(ValueError):
    """The event or an attribute is outside the vocabulary. The message names the event and
    the attribute KEY only — never the offending value, which is exactly what must not leak."""


def validate(event: UsageEvent | str, attributes: Mapping[str, Any]) -> dict[str, Any]:
    """The attributes as they will be exported, or `UsageEventInvalidError`."""
    try:
        name = UsageEvent(event)
    except ValueError:
        raise UsageEventInvalidError("unknown usage event") from None
    spec = EVENTS[name]
    out: dict[str, Any] = {}
    for key, value in attributes.items():
        vocabulary = spec.get(key)
        if vocabulary is None:
            raise UsageEventInvalidError(
                f"{name.value}: attribute {key!r} is not in the vocabulary"
            )
        if not vocabulary.allows(value):
            raise UsageEventInvalidError(
                f"{name.value}: attribute {key!r} has a value outside its vocabulary"
            )
        out[key] = value
    return out


def describe_events() -> list[dict[str, Any]]:
    """The documented, auditable event list — what the admin page and the ADR show."""
    return [
        {"name": event.value, "attributes": {k: v.describe() for k, v in spec.items()}}
        for event, spec in EVENTS.items()
    ]


def register_mcp_tool_names(names: Sequence[str]) -> None:
    MCP_TOOL_NAMES.names = frozenset(names)


def trigger_kind(triggered_by: str) -> str:
    """The marker prefix (`manual:<user>`, `schedule:<id>`, `adf:<pipeline>:<run>` …) as a
    trigger kind; anything unrecognised reads as `other` rather than leaking the marker."""
    prefix = triggered_by.split(":", 1)[0]
    return prefix if prefix in TRIGGER_KINDS else "other"


# ───────────────────────────── sinks ─────────────────────────────


class JsonlFileSink:
    """Append-only JSON lines, shared by the file log exporter and the file metric exporter."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)

    def write(self, records: Sequence[dict[str, Any]]) -> None:
        if not records:
            return
        text = "".join(json.dumps(r, separators=(",", ":"), sort_keys=True) + "\n" for r in records)
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(text)


def _jsonl_log_exporter(sink: JsonlFileSink) -> Any:
    from opentelemetry.sdk._logs.export import LogRecordExporter, LogRecordExportResult

    class JsonlLogExporter(LogRecordExporter):  # type: ignore[misc]  # base is Any (follow_imports=skip)
        def export(self, batch: Sequence[Any]) -> Any:
            sink.write(
                [
                    {
                        "type": "event",
                        "event_name": r.log_record.event_name,
                        "time_unix_nano": r.log_record.timestamp or r.log_record.observed_timestamp,
                        "attributes": dict(r.log_record.attributes or {}),
                        "resource": dict(r.resource.attributes),
                    }
                    for r in batch
                ]
            )
            return LogRecordExportResult.SUCCESS

        def force_flush(self, timeout_millis: int = 30_000) -> bool:
            return True

        def shutdown(self) -> None:
            return None

    return JsonlLogExporter()


def _jsonl_metric_exporter(sink: JsonlFileSink) -> Any:
    from opentelemetry.sdk.metrics import Counter
    from opentelemetry.sdk.metrics.export import (
        AggregationTemporality,
        MetricExporter,
        MetricExportResult,
    )

    class JsonlMetricExporter(MetricExporter):  # type: ignore[misc]  # base is Any (follow_imports=skip)
        def __init__(self) -> None:
            super().__init__(preferred_temporality={Counter: AggregationTemporality.DELTA})

        def export(self, metrics_data: Any, timeout_millis: float = 10_000, **_: Any) -> Any:
            lines = [
                {
                    "type": "metric",
                    "name": metric.name,
                    "attributes": dict(point.attributes),
                    "value": point.value,
                    "start_time_unix_nano": point.start_time_unix_nano,
                    "time_unix_nano": point.time_unix_nano,
                    "resource": dict(rm.resource.attributes),
                }
                for rm in metrics_data.resource_metrics
                for sm in rm.scope_metrics
                for metric in sm.metrics
                for point in metric.data.data_points
            ]
            sink.write(lines)
            return MetricExportResult.SUCCESS

        def force_flush(self, timeout_millis: float = 10_000) -> bool:
            return True

        def shutdown(self, timeout_millis: float = 30_000, **_: Any) -> None:
            return None

    return JsonlMetricExporter()


# ───────────────────────────── provider state ─────────────────────────────


@dataclass
class _State:
    service_name: str
    logger_provider: Any
    meter_provider: Any
    logger: Any
    counter: Any
    metric_readers: list[Any]


_state: _State | None = None
_rejected_warned: set[str] = set()


def configured() -> bool:
    return _state is not None


SIGN_IN_WINDOW_S: Final = 8 * 3600
_seen: dict[str, float] = {}
_seen_lock = threading.Lock()


def first_in_window(key: str, window_s: float = SIGN_IN_WINDOW_S) -> bool:
    """True the first time `key` is seen in this process within `window_s`. The key stays in
    memory and is never exported; it turns a per-request signal into a per-session one."""
    if _state is None:
        return False
    now = time.monotonic()
    with _seen_lock:
        if len(_seen) > 10_000:
            for stale in [k for k, until in _seen.items() if until <= now]:
                del _seen[stale]
        if _seen.get(key, 0.0) > now:
            return False
        _seen[key] = now + window_s
        return True


def configure(
    service_name: str,
    settings: Settings | None = None,
    *,
    log_exporters: Sequence[Any] | None = None,
    metric_readers: Sequence[Any] | None = None,
) -> bool:
    """Build the providers for the configured sink; no-op when `TELEMETRY_SINK=off` or
    already configured. Explicit exporters/readers (tests) replace the sink's own. A sink
    that cannot be built (unwritable path, SDK drift) logs and leaves telemetry OFF — the
    same posture as the log bridge: observability never takes the process down."""
    global _state
    if _state is not None:
        return True
    settings = settings or get_settings()
    sink = settings.telemetry_sink
    if sink == "off" and log_exporters is None and metric_readers is None:
        return False
    try:
        from opentelemetry.sdk._logs import LoggerProvider
        from opentelemetry.sdk._logs.export import (
            BatchLogRecordProcessor,
            SimpleLogRecordProcessor,
        )
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

        resource = otel.build_resource(service_name)
        processors: list[Any] = []
        readers: list[Any] = list(metric_readers or ())
        if log_exporters is not None:
            processors = [SimpleLogRecordProcessor(e) for e in log_exporters]
        elif sink == "file":
            file_sink = JsonlFileSink(Path(settings.telemetry_file_path))
            processors = [SimpleLogRecordProcessor(_jsonl_log_exporter(file_sink))]
            if metric_readers is None:
                readers = [
                    PeriodicExportingMetricReader(
                        _jsonl_metric_exporter(file_sink),
                        export_interval_millis=settings.telemetry_export_interval_s * 1000,
                    )
                ]
        elif sink == "otlp":
            processors = [BatchLogRecordProcessor(e) for e in otel.build_log_exporters(settings)]
            if metric_readers is None:
                readers = [
                    PeriodicExportingMetricReader(
                        e, export_interval_millis=settings.telemetry_export_interval_s * 1000
                    )
                    for e in otel.build_metric_exporters(settings)
                ]
        logger_provider = LoggerProvider(resource=resource)
        for processor in processors:
            logger_provider.add_log_record_processor(processor)
        meter_provider = MeterProvider(resource=resource, metric_readers=readers)
        counter = meter_provider.get_meter(METER_NAME).create_counter(
            COUNTER_NAME, unit="{event}", description="Product-usage events (ADR 0050)"
        )
        _state = _State(
            service_name=service_name,
            logger_provider=logger_provider,
            meter_provider=meter_provider,
            logger=logger_provider.get_logger(METER_NAME),
            counter=counter,
            metric_readers=readers,
        )
        log.info("usage_telemetry_configured", service_name=service_name, sink=sink)
        return True
    except Exception:
        log.warning("usage_telemetry_setup_failed", service_name=service_name, exc_info=True)
        return False


def flush() -> None:
    if _state is None:
        return
    _state.logger_provider.force_flush()
    _state.meter_provider.force_flush()


def reset() -> None:
    """Shut the providers down and forget them (process exit, tests)."""
    global _state
    state, _state = _state, None
    _rejected_warned.clear()
    _seen.clear()
    if state is None:
        return
    try:
        state.logger_provider.shutdown()
        state.meter_provider.shutdown()
    except Exception:
        log.warning("usage_telemetry_shutdown_failed", exc_info=True)


def emit(event: UsageEvent, *, session: Session | None = None, **attributes: Any) -> bool:
    """Record one occurrence; True when it reached the providers. Never raises: a telemetry
    fault must not fail the request, run or tool call it rides on."""
    if _state is None:
        return False
    try:
        attrs = validate(event, attributes)
    except UsageEventInvalidError as exc:
        if event.value not in _rejected_warned:
            _rejected_warned.add(event.value)
            log.warning("usage_telemetry_event_rejected", usage_event=event.value, reason=str(exc))
        return False
    try:
        from backend.app.services import telemetry_settings_service

        if not telemetry_settings_service.opt_in(session):
            return False
        exported = {"event": event.value, **attrs}
        _state.counter.add(1, attributes=exported)
        _state.logger.emit(
            event_name=EVENT_NAME_PREFIX + event.value, body=event.value, attributes=exported
        )
        return True
    except Exception:
        log.warning("usage_telemetry_emit_failed", usage_event=event.value, exc_info=True)
        return False
