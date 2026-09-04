"""Pure formatting helpers shared by the Slack + email renderers (#416)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.app.alerting.base import (
    CheckReport,
    ConnectionHealthReport,
    IncidentCard,
    PollStalenessReport,
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
    """A GX observed/expected dict as a compact ``k=v, k=v`` string."""
    if not mapping:
        return ""
    if set(mapping) == {"observed_value"}:
        return _scalar(mapping["observed_value"])
    return ", ".join(f"{key}={_scalar(val)}" for key, val in mapping.items())


def check_sample_note(check: CheckReport) -> str:
    """The redacted failing-sample summary — ``"3.2% unexpected"`` /
    ``"51 unexpected"`` — or ``""`` when there's no sample. Prefers percent; falls
    back to count (a falsy ``0`` count must still render, so test ``is not None``).

    ``sample_suppressed`` (#1873/#1880) is a THIRD reading of an empty
    ``sample_summary``: this deployment's zero-sample privacy mode never
    persisted one for this result, distinct from a genuinely sample-free check —
    an alert must say so rather than silently reading identically to "nothing
    was found". Only overrides an EMPTY summary — a real, populated summary
    (e.g. the privacy switch flipped on after this run persisted its sample)
    still reports its actual content, mirroring `redact_sample_failures_with_state`'s
    own `sample is None` guard.
    """
    if not check.sample_summary and check.sample_suppressed:
        return "sample suppressed (zero-sample privacy mode)"
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
    """A one-line *expected · observed · unexpected* summary for a failing check."""
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


def _upstream_pipeline_clause(pipeline: Any) -> str:
    """Not "unknown": a manually-triggered or scheduled run has no upstream
    pipeline by design (the majority of runs) — that is a normal, understood
    state, not a gap. Only a layer that failed to build is genuinely unknown.
    """
    if pipeline is None:
        return "not pipeline-triggered (manual or scheduled run)"
    if not isinstance(pipeline, dict):
        return "upstream pipeline: unavailable"
    provider = pipeline.get("provider", "?")
    status = pipeline.get("status", "unknown")
    delay = pipeline.get("delay_seconds_vs_history")
    if not isinstance(delay, int | float):
        return f"upstream {provider} run {status}"
    sign = "+" if delay >= 0 else ""
    return f"upstream {provider} run {status} ({sign}{delay:.0f}s vs history)"


def _sibling_failures_clause(siblings: Any) -> str:
    """`sibling_checks` always resolves to a list (possibly empty) unless the
    layer itself raised — so `None` here is a genuine "could not be built",
    distinct from a genuinely solo check in its run.
    """
    if siblings is None:
        return "same-run siblings: unavailable"
    if not isinstance(siblings, list):
        return "same-run siblings: unavailable"
    if not siblings:
        return "no other checks in this run"
    failing = [s for s in siblings if isinstance(s, dict) and s.get("status") not in (None, "pass")]
    if not failing:
        return f"{len(siblings)} other check(s) in this run, all passing"
    return f"{len(failing)}/{len(siblings)} other check(s) in this run also failing"


def _blast_radius_clause(blast: Any) -> str:
    """An empty list here does NOT mean "nothing downstream is affected" — it
    can equally mean the asset was never resolved or this workspace has no
    lineage recorded at all (the #828 class); never claim the all-clear.
    """
    if blast is None:
        return "downstream impact: unavailable"
    if not isinstance(blast, list):
        return "downstream impact: unavailable"
    if not blast:
        return "no downstream lineage recorded"
    return f"{len(blast)} downstream asset(s) potentially affected"


def evidence_summary_clause(evidence: dict[str, Any] | None) -> str:
    """The deterministic "why" clause appended to `incident_line` (#1647) —
    built purely from the evidence card's own layers, no LLM involved (works
    with none configured, per ADR 0042's default-off rule). Each piece states
    its own absence explicitly rather than being silently dropped, so a
    reader never mistakes "nothing to show" for "nothing happened" — the same
    discipline the MCP `get_incident` docstring already applies to this card.
    """
    if not isinstance(evidence, dict):
        return "evidence: unavailable"
    parts = [
        _upstream_pipeline_clause(evidence.get("upstream_pipeline_run")),
        _sibling_failures_clause(evidence.get("sibling_checks")),
        _blast_radius_clause(evidence.get("downstream_blast_radius")),
    ]
    return "; ".join(parts)


#: A stored narrative's `summary`/`cause` can each run to hundreds of characters
#: (`llm_rca._SUMMARY_MAX_CHARS`/`_CAUSE_MAX_CHARS`), and up to `_MAX_CHECK_LINES`
#: (10, `slack.py`) incident lines get joined into ONE Slack Block Kit section,
#: which has a hard 3000-character limit — an oversized clause would fail the
#: whole alert delivery, not just truncate one incident's detail. Capped well
#: under a tenth of that budget per clause.
_MAX_NARRATIVE_CLAUSE_CHARS = 220


def narrative_clause(narrative: dict[str, Any] | None) -> str:
    """The RCA narrative's (#1633) one-line takeaway, tagged with which
    evidence layer(s) its top-ranked hypothesis rests on. `""` when no
    narrative has ever been generated for this incident — RCA is strictly
    on-demand, so this is the common case, not a missing one.
    """
    if not isinstance(narrative, dict):
        return ""
    summary = narrative.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return ""
    hypotheses = narrative.get("ranked_hypotheses")
    top = hypotheses[0] if isinstance(hypotheses, list) and hypotheses else None
    if not isinstance(top, dict):
        text = f"AI summary: {summary.strip()}"
    else:
        cause = top.get("cause")
        confidence = top.get("confidence", "?")
        refs = top.get("evidence_refs")
        ref_text = f" (via {', '.join(refs)})" if isinstance(refs, list) and refs else ""
        if not isinstance(cause, str) or not cause.strip():
            text = f"AI summary: {summary.strip()}"
        else:
            text = (
                f"AI summary: {summary.strip()} — top cause ({confidence}): "
                f"{cause.strip()}{ref_text}"
            )
    if len(text) <= _MAX_NARRATIVE_CLAUSE_CHARS:
        return text
    return text[: _MAX_NARRATIVE_CLAUSE_CHARS - 1] + "…"


def incident_line(card: IncidentCard) -> str:
    """A one-line incident reference for an alert (ADR 0034 #761), plus the
    deterministic evidence summary (#1647) and, when one exists, the RCA
    narrative's takeaway (#1633) —
    ``"Incident 1a2b3c4d (not-null id) — open, new · no other checks in this
    run; ..."``.
    """
    marker = "new" if card.is_new else f"occurrence {card.occurrence_count}"
    line = f"Incident {card.incident_id.hex[:8]} ({card.check_name}) — {card.status}, {marker}"
    summary = evidence_summary_clause(card.evidence)
    if summary:
        line = f"{line} · {summary}"
    narrative = narrative_clause(card.narrative)
    if narrative:
        line = f"{line} · {narrative}"
    return line


def triggered_source(triggered_by: str | None) -> str:
    """Friendly trigger source: ``Schedule`` / ``ADF`` / ``Airflow`` / ``dbt`` /
    ``Manual`` (from the ``<provider>:...`` prefix), else the raw prefix.
    """
    if not triggered_by:
        return "Manual"
    prefix = triggered_by.split(":", 1)[0]
    return _TRIGGER_LABELS.get(prefix, prefix)


def format_duration(seconds: float | None) -> str | None:
    """Human duration: ``"4.2s"`` under a minute, else ``"2m 3s"``. ``None`` in →
    ``None`` out (the caller omits the field).
    """
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
    all-clear (recovery edge).
    """
    if not report.is_failing:
        return "Polling has resumed; pipeline runs are being ingested again."
    return (
        "While this poll is down, pipeline runs are not ingested, suites bound to this "
        "connection are not triggered, and any lineage it feeds goes stale."
    )


def staleness_headline(report: PollStalenessReport) -> str:
    """One-line summary of the workspace poll-staleness edge (#1052) — shared by all
    channels, like :func:`health_headline`.
    """
    if not report.is_failing:
        return "DataQ — orchestration polling recovered (workspace-wide)"
    return "DataQ — orchestration polling appears DEAD (workspace-wide)"


def staleness_facts(report: PollStalenessReport) -> list[tuple[str, str]]:
    """``(label, value)`` pairs for the poll-staleness edge, mirroring :func:`health_facts`."""
    pairs: list[tuple[str, str | None]] = [
        ("Orchestration connections", str(report.connection_count)),
        (
            "Most recent poll (any connection)",
            _format_timestamp(report.most_recent_polled_at) or "never",
        ),
        (
            "Staleness threshold",
            format_duration(float(report.threshold_seconds)) if report.is_failing else None,
        ),
    ]
    return [(label, value) for label, value in pairs if value]


def staleness_impact(report: PollStalenessReport) -> str:
    """What a dead polling loop costs, or the all-clear."""
    if not report.is_failing:
        return "Poll writes are current again; the worker loop is executing."
    return (
        "No orchestration connection has been polled within the threshold. This is a "
        "worker/broker/beat liveness failure, not a single connection: pipeline runs are "
        "not ingested, bound suites are not triggered, and per-connection health alerts "
        "cannot fire — this alert comes from the API process for exactly that reason."
    )


def run_metadata(report: RunReport) -> list[tuple[str, str]]:
    """``(label, value)`` pairs for the run's metadata row — env, trigger source,
    start time, duration — omitting any that aren't set. Consumed as Slack fields
    and email table rows so both channels show the same metadata.
    """
    pairs: list[tuple[str, str | None]] = [
        ("Owner", report.owner),
        ("Environment", report.env),
        ("Triggered by", triggered_source(report.triggered_by)),
        ("Started", _format_timestamp(report.started_at)),
        ("Duration", format_duration(report.duration_seconds)),
    ]
    return [(label, value) for label, value in pairs if value]
