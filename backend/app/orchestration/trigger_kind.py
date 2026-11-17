"""The kind of source that started a run, named from its `triggered_by` marker's prefix.

Pure: no database and no ORM, so the alert renderer can use it without importing the services
layer. The one mapping from a marker to a label, shared by the runs table
(`services.trigger_labels`, which adds the person's or the upstream suite's name) and by an alert
(which has no viewer to scope a name to, and goes to a channel, so it carries the kind only).
"""

from __future__ import annotations

from backend.app.db.models import SUITE_TRIGGER_PROVIDER
from backend.app.orchestration.registry import provider_display_names

#: Markers whose remainder is a user id: the kinds a person, or the probe, starts.
USER_KINDS = {"manual": "Manual", "mcp": "MCP", "probe": "Probe"}
SUITE_LABEL = "Suite"
SCHEDULE_LABEL = "Schedule"


def trigger_kind_label(marker: str) -> str | None:
    """``Schedule`` / ``Manual`` / ``MCP`` / ``Probe`` / ``Suite`` / a provider's display name,
    from the marker's prefix alone, or ``None`` for a prefix that is none of these. A
    provider's name is read from the registry on each call, not copied at import.
    """
    kind = marker.partition(":")[0]
    if kind in USER_KINDS:
        return USER_KINDS[kind]
    if kind == "schedule":
        return SCHEDULE_LABEL
    if kind == SUITE_TRIGGER_PROVIDER:
        return SUITE_LABEL
    return provider_display_names().get(kind)
