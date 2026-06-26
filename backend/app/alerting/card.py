"""Render a ``RunReport`` as a Microsoft Teams message (Adaptive Card).

Pure: the boundary DTO in, the JSON body a Teams incoming webhook accepts out —
no I/O, no GX, no ORM. The card carries what an on-call needs to triage a failed
run: suite + datasource + target, the pass/fail tally, and per-failing-check
observed-vs-expected. Everything here is already redacted upstream
(``CheckReport.sample_summary`` is counts-only; raw rows never reach the seam),
so the renderer can format every field without a second PII pass.
"""

from __future__ import annotations

from typing import Any

from backend.app.alerting.base import FAILING_TIERS, CheckReport, RunReport

# Teams' incoming-webhook (Workflows) body shape: a message wrapping one or more
# adaptive-card attachments.
_ADAPTIVE_CARD_VERSION = "1.4"
_ADAPTIVE_CARD_SCHEMA = "http://adaptivecards.io/schemas/adaptive-card.json"
_CARD_CONTENT_TYPE = "application/vnd.microsoft.card.adaptive"

# Keep the card a sane size — a suite with hundreds of failing checks shouldn't
# produce a multi-megabyte card. Overflow is summarised as "+N more".
_MAX_CHECK_ROWS = 10

# Worst-severity → accent colour for the title (Adaptive Card named colours).
_SEVERITY_COLOR = {"critical": "attention", "fail": "attention", "warn": "warning"}


def render_teams_message(report: RunReport) -> dict[str, Any]:
    """The full Teams webhook payload for ``report`` (message + adaptive card)."""
    return {
        "type": "message",
        "attachments": [
            {"contentType": _CARD_CONTENT_TYPE, "content": _adaptive_card(report)},
        ],
    }


def _adaptive_card(report: RunReport) -> dict[str, Any]:
    body: list[dict[str, Any]] = [
        _title_block(report),
        _subtitle_block(report),
        _facts_block(report),
    ]
    failing = [c for c in report.checks if c.status in FAILING_TIERS]
    if failing:
        body.append(_text("Failing checks", weight="Bolder", spacing="Medium"))
        body.extend(_check_block(c) for c in failing[:_MAX_CHECK_ROWS])
        overflow = len(failing) - _MAX_CHECK_ROWS
        if overflow > 0:
            body.append(_text(f"+{overflow} more", is_subtle=True))
    return {
        "type": "AdaptiveCard",
        "$schema": _ADAPTIVE_CARD_SCHEMA,
        "version": _ADAPTIVE_CARD_VERSION,
        "body": body,
    }


def _title_block(report: RunReport) -> dict[str, Any]:
    color = _SEVERITY_COLOR.get(report.worst_severity or "", "attention")
    return _text(report.suite_name, size="Large", weight="Bolder", color=color)


def _subtitle_block(report: RunReport) -> dict[str, Any]:
    # An operational run failure (adapter raised) has no result rows to tally.
    if report.run_status == "failed" and not report.checks:
        return _text("Run failed to execute", is_subtle=True)
    return _text(f"{report.failed_checks} of {report.total_checks} checks failed", is_subtle=True)


def _facts_block(report: RunReport) -> dict[str, Any]:
    facts = [
        {"title": "Datasource", "value": report.datasource_type or "—"},
        {"title": "Target", "value": report.target_label},
        {"title": "Run", "value": report.run_status},
        {"title": "Severity", "value": report.worst_severity or "—"},
    ]
    if report.finished_at is not None:
        facts.append({"title": "Finished", "value": report.finished_at.isoformat()})
    return {"type": "FactSet", "facts": facts}


def _check_block(check: CheckReport) -> dict[str, Any]:
    detail = (
        f"observed {_compact(check.observed_value)} vs expected {_compact(check.expected_value)}"
    )
    sample = check.sample_summary or {}
    if "unexpected_count" in sample:
        detail += f" · {sample['unexpected_count']} rows"
    return _text(
        f"**{check.check_name}** ({check.status}) — {check.expectation_type}\n\n{detail}",
        wrap=True,
        spacing="Small",
    )


def _compact(value: dict[str, Any] | None) -> str:
    """A short ``key=val`` rendering of a small GX observed/expected dict."""
    if not value:
        return "—"
    return ", ".join(f"{k}={v}" for k, v in value.items())


def _text(text: str, **props: Any) -> dict[str, Any]:
    """An Adaptive Card ``TextBlock``. ``is_subtle``/``weight``/``size``/``color``/
    ``spacing``/``wrap`` map to their camelCase card properties."""
    block: dict[str, Any] = {"type": "TextBlock", "text": text}
    if props.pop("wrap", False):
        block["wrap"] = True
    if props.pop("is_subtle", False):
        block["isSubtle"] = True
    for key in ("weight", "size", "color", "spacing"):
        if key in props:
            block[key] = props[key]
    return block
