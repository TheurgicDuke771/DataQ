"""Severity-aware routing — *whether* and *how loudly* to alert on a run."""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.alerting.base import RunReport

# Urgency levels, quietest first. The card maps these to colour + escalation.
QUIET = "quiet"
STANDARD = "standard"
CRITICAL = "critical"

# Per-suite delivery policies.
FAIL_ONLY = "fail"
WARN_PLUS = "warn"
ALWAYS = "always"
DEFAULT_POLICY = WARN_PLUS


@dataclass(frozen=True)
class Route:
    """The routing decision for one run."""

    should_send: bool
    urgency: str
    mention_channel: bool


def _urgency(report: RunReport) -> tuple[str, bool]:
    """(urgency, mention_channel) from the run's worst severity."""
    if report.worst_severity == CRITICAL:
        return CRITICAL, True
    if report.worst_severity == "fail" or report.run_status == "failed":
        return STANDARD, False
    return QUIET, False  # warn or clean


def _should_send(report: RunReport, policy: str) -> bool:
    """Apply the per-suite delivery threshold."""
    if policy == ALWAYS:
        return True
    if report.run_status == "failed":
        # Operational failure always alerts, regardless of threshold — including a failed run
        # carrying only warn-tier rows, which `fail` would otherwise gate out on severity (#383).
        return True
    worst = report.worst_severity
    if worst is None:
        return False  # clean run
    if policy == FAIL_ONLY:
        return worst in ("fail", CRITICAL)
    return worst in ("warn", "fail", CRITICAL)  # WARN_PLUS


def route_for(report: RunReport, policy: str = DEFAULT_POLICY) -> Route:
    """Decide whether + how loudly to alert on ``report`` under ``policy``."""
    urgency, mention = _urgency(report)
    return Route(should_send=_should_send(report, policy), urgency=urgency, mention_channel=mention)
