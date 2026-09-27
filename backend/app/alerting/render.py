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
from backend.app.services.incident_evidence import blast_radius_assets_and_qualifiers

# Longer scalars (a big value_set, a stringified row) are truncated so one check
# can't blow up a card; the full detail lives on the linked run-detail page.
_MAX_SCALAR = 60
# How many redacted failing-sample values to preview inline in an alert.
_MAX_SAMPLE_VALUES = 3
# The lineage-source qualifier clause is capped independently of `_MAX_SCALAR` — it can
# name several sources, and this whole clause still has to fit a Slack block budget (#1990).
_MAX_QUALIFIER_CLAUSE = 200

# triggered_by is stored as "<provider>:<...>" (schedule/adf/airflow/dbt) or NULL
# for a manual run. Map the prefix to a friendly source name for the alert.
_TRIGGER_LABELS = {
    "schedule": "Schedule",
    "adf": "ADF",
    "airflow": "Airflow",
    "dbt": "dbt",
    "manual": "Manual",
}


def format_number(value: float | int) -> str:
    """At most 2 decimals, thousands separators, trailing zeros dropped —
    ``99.4232987`` → ``99.42``, ``34480`` → ``34,480``, ``10.0`` → ``10``. A
    non-zero value that 2 decimals would erase keeps 2 significant figures
    (``0.004``, ``0.0021``) so a real failure never reads as ``0``.
    """
    if isinstance(value, bool):
        return str(value)
    number = float(value)
    if number != 0 and abs(number) < 0.01:
        return f"{number:.2g}"
    text = f"{round(number, 2):,.2f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _scalar(value: Any) -> str:
    """A compact one-line string for a JSON scalar, truncated if long."""
    text = format_number(value) if isinstance(value, float) else str(value)
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
    if isinstance(pct, int | float):
        return f"{format_number(pct)}% unexpected"
    count = sample.get("unexpected_count")
    if isinstance(count, int | float):
        return f"{format_number(count)} unexpected"
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

    When the lineage source(s) behind the graph are failing, stale, coarse, or
    have a suspended prune, that qualifier (#1990, the same wording
    `get_asset`'s `lineage.qualified_by` carries) is appended — a prune
    suspension names a risk of EXTRA edges, the opposite direction from the
    rest, so it is never silently folded into "may be incomplete".
    """
    if blast is None:
        return "downstream impact: unavailable"
    if not isinstance(blast, dict | list):
        return "downstream impact: unavailable"
    assets, qualifiers = blast_radius_assets_and_qualifiers(blast)
    base = (
        "no downstream lineage recorded"
        if not assets
        else f"{len(assets)} downstream asset(s) potentially affected"
    )
    if qualifiers:
        clause = "; ".join(qualifiers)
        if len(clause) > _MAX_QUALIFIER_CLAUSE:
            clause = clause[: _MAX_QUALIFIER_CLAUSE - 1] + "…"
        base += f" — lineage source caveat: {clause}"
    return base


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


_ROW_RULE_PHRASES = {
    "expect_column_values_to_be_unique": "have a duplicate {col}",
    "expect_column_values_to_not_be_null": "are missing {col}",
    "expect_column_values_to_be_null": "have a {col} where none is expected",
    "expect_column_values_to_be_between": "have {col} outside the allowed range{range}",
    "expect_column_values_to_be_in_set": "have a {col} that isn't on the allowed list",
    "expect_column_values_to_not_be_in_set": "have a {col} that is on the blocked list",
    "expect_column_values_to_match_regex": "have {col} in an unexpected format",
    "expect_column_values_to_not_match_regex": "have {col} in a disallowed format",
    "expect_column_values_to_match_regex_list": "have {col} in an unexpected format",
    "expect_column_values_to_not_match_regex_list": "have {col} in a disallowed format",
    "expect_column_value_lengths_to_be_between": "have {col} with an unexpected length",
    "expect_column_value_lengths_to_equal": "have {col} with an unexpected length",
}


_SINGULAR = {"have": "has", "are": "is"}


def _bound(value: Any) -> str:
    return format_number(value) if isinstance(value, int | float) else str(value)


def _range_text(expected: dict[str, Any]) -> str:
    low, high = expected.get("min_value"), expected.get("max_value")
    if low is not None and high is not None:
        return f" ({_bound(low)} to {_bound(high)})"
    if low is not None:
        return f" (at least {_bound(low)})"
    if high is not None:
        return f" (at most {_bound(high)})"
    return ""


def _friendly_age(hours: float) -> str:
    if hours < 1:
        return f"{format_number(hours * 60)} minutes"
    if hours < 48:
        return f"{format_number(hours)} hours"
    return f"{format_number(hours / 24)} days"


def _friendly_timestamp(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        when = datetime.fromisoformat(value)
    except ValueError:
        return None
    return when.strftime("%d %b %Y, %H:%M") + (" UTC" if when.utcoffset() is not None else "")


def _rows_phrase(sample: dict[str, Any]) -> str | None:
    count, pct = sample.get("unexpected_count"), sample.get("unexpected_percent")
    count = count if isinstance(count, int | float) else None
    pct = pct if isinstance(pct, int | float) else None
    if count is not None:
        rows = f"{format_number(count)} {'row' if count == 1 else 'rows'}"
        return f"{rows} ({format_number(pct)}%)" if pct is not None else rows
    if pct is not None:
        return f"{format_number(pct)}% of rows"
    return None


def plain_check_summary(check: CheckReport) -> str:
    """One plain-English sentence a non-engineer can act on (#2108), or ``""``
    when the result carries nothing to describe — the caller then falls back
    to the technical detail. Never states a count the result doesn't carry.
    """
    if check.status == "error":
        return "This check couldn't run, so this data wasn't verified. Open the run for the reason."
    if check.status == "skip":
        return "This check was skipped, so this data wasn't verified."
    expected = check.expected_value or {}
    observed = check.observed_value or {}
    monitor = expected.get("monitor") or check.expectation_type.removeprefix("monitor:")
    if monitor == "freshness" and isinstance(observed.get("age_hours"), int | float):
        text = f"The newest record is {_friendly_age(observed['age_hours'])} old"
        when = _friendly_timestamp(observed.get("max_timestamp"))
        return text + (f" (last updated {when})." if when else ".")
    if check.expectation_type == "expect_table_row_count_to_be_between":
        rows = observed.get("observed_value")
        if isinstance(rows, int | float):
            return (
                f"The table has {format_number(rows)} rows, outside the expected range"
                f"{_range_text(expected)}."
            )
    rows_text = _rows_phrase(check.sample_summary or {})
    if check.expectation_type == "unexpected_rows_expectation":
        count = observed.get("observed_value")
        if isinstance(count, int | float):
            noun = "row matches" if count == 1 else "rows match"
            return f"{format_number(count)} {noun} this rule's failure condition."
    phrase = _ROW_RULE_PHRASES.get(check.expectation_type)
    column = expected.get("column")
    if phrase and isinstance(column, str) and rows_text:
        text = phrase.format(col=column, range=_range_text(expected))
        if (check.sample_summary or {}).get("unexpected_count") == 1:
            verb, _, rest = text.partition(" ")
            text = f"{_SINGULAR.get(verb, verb)} {rest}"
        return f"{rows_text} {text}."
    if rows_text:
        return f"{rows_text} didn't meet this rule."
    return ""


def _lineage_caveat(qualifiers: list[str]) -> str:
    """One honest sentence for the qualifiers `_blast_radius_clause` lists in
    full, keeping what each one actually says: a failing refresh, a stale one, a
    coarse (view-level) one, and a suspended prune — the last risks EXTRA
    tables, the rest MISSING or outdated ones, never merged into one direction.
    """
    failing = sum(1 for q in qualifiers if "failing" in q)
    stale = sum(1 for q in qualifiers if "has not refreshed recently" in q)
    coarse = sum(1 for q in qualifiers if "is coarse" in q)
    extra = any("pruned removed edges" in q for q in qualifiers)
    unknown = (
        len(qualifiers)
        - failing
        - stale
        - coarse
        - sum(1 for q in qualifiers if "pruned removed edges" in q)
    )

    def sources(n: int) -> str:
        return f"{n} lineage source{'s' if n != 1 else ''}"

    problems = []
    if failing:
        problems.append(f"{sources(failing)} couldn't be refreshed")
    if stale:
        problems.append(
            f"{sources(stale)} {'hasn' if stale == 1 else 'haven'}'t refreshed recently"
        )
    if coarse:
        problems.append(
            f"{sources(coarse)} only record{'s' if coarse == 1 else ''} view-level lineage"
        )
    if unknown > 0:
        problems.append(f"{sources(unknown)} reported a problem")
    notes = []
    if problems:
        notes.append(", ".join(problems) + ", so this list may be incomplete or out of date")
    if extra:
        notes.append("it may also include tables that no longer depend on this one")
    return "; ".join(notes)


def incident_facts(card: IncidentCard) -> list[tuple[str, str]]:
    """The incident context as short labelled facts (#2108) — the same evidence
    `incident_line` packs into one line, each layer still stating its own
    absence rather than being dropped (#1647).
    """
    facts: list[tuple[str, str]] = [
        (
            "Incident",
            f"{card.incident_id.hex[:8]} — "
            + (
                "new problem, first time this check has failed."
                if card.is_new
                else f"ongoing, failed on {format_number(card.occurrence_count)} runs so far."
            ),
        )
    ]
    evidence = card.evidence if isinstance(card.evidence, dict) else None
    if evidence is None:
        facts.append(("Context", "Not available for this incident."))
    else:
        pipeline = evidence.get("upstream_pipeline_run")
        if pipeline is None:
            facts.append(("Triggered by", "A manual or scheduled run (no upstream pipeline)."))
        elif not isinstance(pipeline, dict):
            facts.append(("Triggered by", "Not available."))
        else:
            facts.append(
                (
                    "Triggered by",
                    f"{pipeline.get('provider', 'pipeline')} run "
                    f"({pipeline.get('status', 'status unknown')}).",
                )
            )
        siblings = evidence.get("sibling_checks")
        if isinstance(siblings, list):
            failing = [
                s for s in siblings if isinstance(s, dict) and s.get("status") not in (None, "pass")
            ]
            if not siblings:
                text = "This was the only check in the run."
            elif failing:
                text = f"{len(failing)} of {len(siblings)} other checks also failed."
            else:
                text = f"All {len(siblings)} other checks passed."
            facts.append(("Same run", text))
        else:
            facts.append(("Same run", "Not available."))
        blast = evidence.get("downstream_blast_radius")
        if isinstance(blast, dict | list):
            assets, qualifiers = blast_radius_assets_and_qualifiers(blast)
            names = [a.get("name") for a in assets if isinstance(a, dict) and a.get("name")]
            if assets:
                text = f"May affect {len(assets)} downstream table{'s' if len(assets) != 1 else ''}"
                if names:
                    more = len(assets) - min(len(names), 3)
                    text += ": " + ", ".join(str(n) for n in names[:3])
                    text += f" and {more} more" if more > 0 else ""
                text += "."
            else:
                text = "No downstream tables are recorded."
            caveat = _lineage_caveat(qualifiers)
            if caveat:
                text += f" Note: {caveat}."
            facts.append(("Downstream", text))
        else:
            facts.append(("Downstream", "Not available."))
    narrative = narrative_clause(card.narrative)
    if narrative:
        facts.append(("AI summary", narrative.removeprefix("AI summary: ")))
    return facts


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
