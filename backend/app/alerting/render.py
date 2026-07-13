"""Pure formatting helpers shared by the Slack + email renderers (#416).

Turn the already-redacted ``RunReport`` / ``CheckReport`` DTOs into the small
strings the channel renderers assemble: run metadata (env, trigger, when, how
long) and a per-check *expected-vs-observed + redacted-sample* detail line. Kept
here so the two channels stay consistent and neither re-implements the
formatting — pure functions (DTO in, ``str`` out), no I/O, no ORM, and they only
ever read the redacted fields the builder produced, so nothing here can leak PII.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.app.alerting.base import (
    CheckReport,
    ConnectionHealthReport,
    IncidentCard,
    RunReport,
)

# Longer scalars (a big value_set, a stringified row) are truncated so one check
# can't blow up a card; the full detail lives on the linked run-detail page.
_MAX_SCALAR = 60
# How many redacted failing-sample values to preview inline in an alert.
_MAX_SAMPLE_VALUES = 3

# triggered_by is stored as "<provider>:<...>" (schedule/adf/airflow/dbt) or NULL
# for a manual run. Map the prefix to a friendly source name for the alert.
_TRIGGER_LABELS = {
    "schedule": "Schedule",
    "adf": "ADF",
    "airflow": "Airflow",
    "dbt": "dbt",
    "manual": "Manual",
}


def _scalar(value: Any) -> str:
    """A compact one-line string for a JSON scalar, truncated if long."""
    text = f"{value:g}" if isinstance(value, float) else str(value)
    return text if len(text) <= _MAX_SCALAR else text[: _MAX_SCALAR - 1] + "…"


def _compact(mapping: dict[str, Any] | None) -> str:
    """A GX observed/expected dict as a compact ``k=v, k=v`` string.

    Unwraps the common ``{"observed_value": x}`` single-key shape to just ``x``;
    otherwise joins the pairs. Empty/``None`` → ``""``.
    """
    if not mapping:
        return ""
    if set(mapping) == {"observed_value"}:
        return _scalar(mapping["observed_value"])
    return ", ".join(f"{key}={_scalar(val)}" for key, val in mapping.items())


def check_sample_note(check: CheckReport) -> str:
    """The redacted failing-sample summary — ``"3.2% unexpected"`` /
    ``"51 unexpected"`` — or ``""`` when there's no sample. Prefers percent; falls
    back to count (a falsy ``0`` count must still render, so test ``is not None``)."""
    sample = check.sample_summary or {}
    pct = sample.get("unexpected_percent")
    if pct is not None:
        return f"{pct}% unexpected"
    count = sample.get("unexpected_count")
    if count is not None:
        return f"{count} unexpected"
    return ""


def check_sample_values(check: CheckReport) -> str:
    """A short preview of the tested column's **already-redacted** failing values —
    ``"e.g. -5, -12, -3"`` — from ``sample_summary['partial_unexpected_list']``, or
    ``""`` when there are none / they're row-dicts (too wide for a one-liner).

    These are whatever ``run_service.redact_sample_failures`` chose to surface
    (non-PII tested-column values, or ``"***"`` masks) — never raw PII: this only
    reads the redacted DTO, it does not re-derive from raw rows.
    """
    values = (check.sample_summary or {}).get("partial_unexpected_list")
    if not isinstance(values, list):
        return ""
    scalars = [v for v in values if not isinstance(v, dict | list)]
    if not scalars:
        return ""
    shown = ", ".join(_scalar(v) for v in scalars[:_MAX_SAMPLE_VALUES])
    extra = len(scalars) - _MAX_SAMPLE_VALUES
    return f"e.g. {shown}" + (f", +{extra} more" if extra > 0 else "")


def check_detail(check: CheckReport) -> str:
    """A one-line *expected · observed · unexpected* summary for a failing check.

    Combines the check's expected kwargs, its observed value (the GX aggregate, or
    the numeric ``metric_value`` when no observed dict was stored), and the redacted
    sample note — whichever are present — into an actionable line like
    ``expected min_value=0 · observed 12 · 3.2% unexpected``.
    """
    parts: list[str] = []
    expected = _compact(check.expected_value)
    if expected:
        parts.append(f"expected {expected}")
    observed = _compact(check.observed_value)
    if observed:
        parts.append(f"observed {observed}")
    elif check.metric_value is not None:
        parts.append(f"observed {_scalar(check.metric_value)}")
    sample = check_sample_note(check)
    if sample:
        parts.append(sample)
    values = check_sample_values(check)
    if values:
        parts.append(values)
    return " · ".join(parts)


def incident_line(card: IncidentCard) -> str:
    """A minimal one-line incident reference for an alert (ADR 0034 #761) —
    ``"Incident 1a2b3c4d (not-null id) — open, new"`` / ``"…, occurrence 3"``.

    Deliberately minimal: the alert stays a per-result notification that
    *references* the stateful incident; rich incident formatting (evidence-card
    excerpts, ack/resolve actions) defers to the #773 navigation-inversion phase.
    Shared by the Slack + email renderers (and summarized into one Teams fact) so
    the channels can't drift.
    """
    marker = "new" if card.is_new else f"occurrence {card.occurrence_count}"
    return f"Incident {card.incident_id.hex[:8]} ({card.check_name}) — {card.status}, {marker}"


def triggered_source(triggered_by: str | None) -> str:
    """Friendly trigger source: ``Schedule`` / ``ADF`` / ``Airflow`` / ``dbt`` /
    ``Manual`` (from the ``<provider>:...`` prefix), else the raw prefix."""
    if not triggered_by:
        return "Manual"
    prefix = triggered_by.split(":", 1)[0]
    return _TRIGGER_LABELS.get(prefix, prefix)


def format_duration(seconds: float | None) -> str | None:
    """Human duration: ``"4.2s"`` under a minute, else ``"2m 3s"``. ``None`` in →
    ``None`` out (the caller omits the field)."""
    if seconds is None:
        return None
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}m {secs}s"


def _format_timestamp(when: datetime | None) -> str | None:
    """A compact UTC-ish timestamp for the alert, or ``None`` when absent."""
    return when.strftime("%Y-%m-%d %H:%M %Z").strip() if when is not None else None


def health_headline(report: ConnectionHealthReport) -> str:
    """The one-line summary of a connection-health edge (#837) — shared by all three
    channels so a Teams title, a Slack header and an email subject can't drift.

    Says what broke *and* what it costs, because "poll failing" alone reads like a
    transient blip: a connection whose poll is down stops ingesting pipeline runs, stops
    firing the suites bound to them, and (for dbt) stops refreshing lineage — which is
    exactly the six days of silent rot #828 came from.
    """
    if not report.is_failing:
        return f"DataQ — {report.connection_name}: orchestration poll recovered"
    return (
        f"DataQ — {report.connection_name}: orchestration poll failing "
        f"({report.consecutive_failures} consecutive failures)"
    )


def health_facts(report: ConnectionHealthReport) -> list[tuple[str, str]]:
    """``(label, value)`` pairs describing a connection-health edge, omitting the ones
    that don't apply (no reason / no failure count on a recovery).

    ``Reason`` is passed through from the report, which carries the **classified**
    failure — this formatter never sees, and so can never leak, the raw exception text.
    """
    pairs: list[tuple[str, str | None]] = [
        ("Connection", report.connection_name),
        ("Provider", report.connection_type),
        ("Reason", report.reason),
        (
            "Consecutive failures",
            str(report.consecutive_failures) if report.is_failing else None,
        ),
        ("Last polled", _format_timestamp(report.last_polled_at)),
    ]
    return [(label, value) for label, value in pairs if value]


def health_impact(report: ConnectionHealthReport) -> str:
    """What the operator loses while this poll is down (failing edge), or the
    all-clear (recovery edge)."""
    if not report.is_failing:
        return "Polling has resumed; pipeline runs are being ingested again."
    return (
        "While this poll is down, pipeline runs are not ingested, suites bound to this "
        "connection are not triggered, and any lineage it feeds goes stale."
    )


def run_metadata(report: RunReport) -> list[tuple[str, str]]:
    """``(label, value)`` pairs for the run's metadata row — env, trigger source,
    start time, duration — omitting any that aren't set. Consumed as Slack fields
    and email table rows so both channels show the same metadata."""
    pairs: list[tuple[str, str | None]] = [
        ("Owner", report.owner),
        ("Environment", report.env),
        ("Triggered by", triggered_source(report.triggered_by)),
        ("Started", _format_timestamp(report.started_at)),
        ("Duration", format_duration(report.duration_seconds)),
    ]
    return [(label, value) for label, value in pairs if value]
