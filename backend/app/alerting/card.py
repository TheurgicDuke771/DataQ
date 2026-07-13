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

from backend.app.alerting import render
from backend.app.alerting.base import (
    FAILING_TIERS,
    CheckReport,
    ConnectionHealthReport,
    RunReport,
)
from backend.app.alerting.routing import QUIET, Route

# Teams' incoming-webhook (Workflows) body shape: a message wrapping one or more
# adaptive-card attachments.
_ADAPTIVE_CARD_VERSION = "1.4"
_ADAPTIVE_CARD_SCHEMA = "http://adaptivecards.io/schemas/adaptive-card.json"
_CARD_CONTENT_TYPE = "application/vnd.microsoft.card.adaptive"

# Keep the card a sane size — a suite with hundreds of failing checks shouldn't
# produce a multi-megabyte card. Overflow is summarised as "+N more".
_MAX_CHECK_ROWS = 10

# Routing urgency → title accent colour (Adaptive Card named colours). A quiet
# (warn) alert is amber and calm; standard/critical are red.
_URGENCY_COLOR = {QUIET: "warning"}
_DEFAULT_COLOR = "attention"


def render_teams_message(report: RunReport, route: Route) -> dict[str, Any]:
    """The full Teams webhook payload for ``report`` (message + adaptive card).

    ``route`` (from ``routing.route_for``) sets the prominence: the title colour
    follows the urgency, and a critical ``mention_channel`` adds a channel-escalation
    banner at the top.
    """
    return {
        "type": "message",
        "attachments": [
            {"contentType": _CARD_CONTENT_TYPE, "content": _adaptive_card(report, route)},
        ],
    }


def render_teams_health_message(report: ConnectionHealthReport) -> dict[str, Any]:
    """The Teams payload for a connection-health edge (#837) — same message envelope
    as a run card, a much smaller body: headline, impact, and the classified facts.

    Pure, and it only reads the report's already-classified ``reason``, so no raw
    exception text (and no credential inside one) can reach the webhook.
    """
    body: list[dict[str, Any]] = [
        _text(
            render.health_headline(report),
            size="Large",
            weight="Bolder",
            color=_DEFAULT_COLOR if report.is_failing else "good",
            wrap=True,
        ),
        _text(render.health_impact(report), is_subtle=True, wrap=True),
        {
            "type": "FactSet",
            "facts": [
                {"title": label, "value": value} for label, value in render.health_facts(report)
            ],
        },
    ]
    card: dict[str, Any] = {
        "type": "AdaptiveCard",
        "$schema": _ADAPTIVE_CARD_SCHEMA,
        "version": _ADAPTIVE_CARD_VERSION,
        "body": body,
    }
    if report.connection_url:
        card["actions"] = [
            {"type": "Action.OpenUrl", "title": "View connection", "url": report.connection_url}
        ]
    return {
        "type": "message",
        "attachments": [{"contentType": _CARD_CONTENT_TYPE, "content": card}],
    }


def _adaptive_card(report: RunReport, route: Route) -> dict[str, Any]:
    body: list[dict[str, Any]] = []
    if route.mention_channel:
        body.append(_text("@channel · CRITICAL", weight="Bolder", color="attention"))
    body += [_title_block(report, route), _subtitle_block(report), _facts_block(report)]
    failing = [c for c in report.checks if c.status in FAILING_TIERS]
    if failing:
        body.append(_text("Failing checks", weight="Bolder", spacing="Medium"))
        body.extend(_check_block(c) for c in failing[:_MAX_CHECK_ROWS])
        overflow = len(failing) - _MAX_CHECK_ROWS
        if overflow > 0:
            body.append(_text(f"+{overflow} more", is_subtle=True))
    card: dict[str, Any] = {
        "type": "AdaptiveCard",
        "$schema": _ADAPTIVE_CARD_SCHEMA,
        "version": _ADAPTIVE_CARD_VERSION,
        "body": body,
    }
    # A "View run" deep link to the run-detail page (when a public base URL is set).
    if report.run_url:
        card["actions"] = [{"type": "Action.OpenUrl", "title": "View run", "url": report.run_url}]
    return card


def _title_block(report: RunReport, route: Route) -> dict[str, Any]:
    # A clean run delivered under the 'always' (heartbeat) policy reads positive;
    # otherwise the title colour follows the alert urgency.
    color = "good" if report.success else _URGENCY_COLOR.get(route.urgency, _DEFAULT_COLOR)
    return _text(report.suite_name, size="Large", weight="Bolder", color=color)


def _subtitle_block(report: RunReport) -> dict[str, Any]:
    if report.success:
        return _text(f"All {report.total_checks} checks passed", is_subtle=True)
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
    # Owner / Environment / Triggered by / Started / Duration — the same shared
    # metadata the Slack + email renderers show (#661, #416 parity).
    facts += [{"title": label, "value": value} for label, value in render.run_metadata(report)]
    # One minimal incident fact (ADR 0034 #761): how many active incidents this
    # run's failing checks reference. Deep incident formatting on the card defers
    # to the #773 navigation-inversion phase.
    if report.incidents:
        new_count = sum(1 for card in report.incidents if card.is_new)
        facts.append(
            {"title": "Incidents", "value": f"{len(report.incidents)} active ({new_count} new)"}
        )
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
