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


@dataclass(frozen=True)
class Route:
    """The routing decision for one run.

    ``should_send`` gates delivery (a clean run is ``False``); ``urgency`` drives
    the card's prominence; ``mention_channel`` escalates a critical breach to the
    whole channel.
    """

    should_send: bool
    urgency: str
    mention_channel: bool


_NO_SEND = Route(should_send=False, urgency=QUIET, mention_channel=False)


def route_for(report: RunReport) -> Route:
    """Decide how to route ``report`` from its worst severity.

    - **critical** → send, critical urgency, escalate to the channel.
    - **fail** (or an operational run failure with no result rows) → send,
      standard urgency.
    - **warn** → send, quiet urgency (no escalation).
    - otherwise (all clean) → don't send.
    """
    if report.worst_severity == CRITICAL:
        return Route(should_send=True, urgency=CRITICAL, mention_channel=True)
    if report.worst_severity == "fail" or report.run_status == "failed":
        return Route(should_send=True, urgency=STANDARD, mention_channel=False)
    if report.worst_severity == "warn":
        return Route(should_send=True, urgency=QUIET, mention_channel=False)
    return _NO_SEND
