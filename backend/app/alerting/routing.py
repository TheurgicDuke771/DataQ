"""Severity-aware routing — *whether* and *how loudly* to alert on a run.

The single policy point between a built ``RunReport`` and the channel: it maps
the run's worst severity to a ``Route`` (send-or-not + urgency + whether to
escalate to the whole channel). The publisher delegates the send decision here,
and the card renders the urgency, so "warn is quiet, fail is standard, critical
pings the channel" lives in one place rather than smeared across the publisher
and the renderer.

Per-suite policy (alert on fail / warn / always) extends this in a later PR by
folding the suite's preference into ``route_for`` — the publisher and card don't
change.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.app.alerting.base import RunReport

# Urgency levels, quietest first. The card maps these to colour + escalation.
QUIET = "quiet"
STANDARD = "standard"
CRITICAL = "critical"

# Per-suite delivery policies. The single source of the allowed values is
# db.models.ALERT_ON_POLICIES (the SuiteNotification CHECK constraint +
# notification_service validation); these are the same values named for
# readability at the routing call sites, pinned to that source by a drift-guard
# test (#388). The default preserves the pre-config behaviour: alert on warn+ but
# not on clean runs.
FAIL_ONLY = "fail"
WARN_PLUS = "warn"
ALWAYS = "always"
DEFAULT_POLICY = WARN_PLUS


@dataclass(frozen=True)
class Route:
    """The routing decision for one run.

    ``should_send`` gates delivery; ``urgency`` drives the card's prominence;
    ``mention_channel`` escalates a critical breach to the whole channel.
    """

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
    """Apply the per-suite delivery threshold.

    ``always`` sends every terminal run (a heartbeat). Otherwise an operational
    run failure always alerts; a clean run never does; and the severity threshold
    decides the rest (``fail`` = fail/critical only, ``warn`` = warn+).
    """
    if policy == ALWAYS:
        return True
    if report.run_status == "failed":
        # Operational failure always alerts, regardless of threshold — including a
        # failed run carrying only warn-tier rows, which `fail` would otherwise gate
        # out on severity (#383). Matches the docstring's "operational run failure
        # always alerts".
        return True
    worst = report.worst_severity
    if worst is None:
        return False  # clean run
    if policy == FAIL_ONLY:
        return worst in ("fail", CRITICAL)
    return worst in ("warn", "fail", CRITICAL)  # WARN_PLUS


def route_for(report: RunReport, policy: str = DEFAULT_POLICY) -> Route:
    """Decide whether + how loudly to alert on ``report`` under ``policy``.

    Urgency/escalation come from the worst severity (``critical`` pings the
    channel, ``fail``/operational is standard, ``warn`` is quiet); the per-suite
    ``policy`` gates *whether* to send (see ``_should_send``).
    """
    urgency, mention = _urgency(report)
    return Route(should_send=_should_send(report, policy), urgency=urgency, mention_channel=mention)
