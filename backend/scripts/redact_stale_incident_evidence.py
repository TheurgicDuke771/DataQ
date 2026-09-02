"""One-time backfill for #1772: mask any pre-fix incident evidence still on disk.

Run once, against each deployment, after the build-time G3 redaction fix lands:

    python -m backend.scripts.redact_stale_incident_evidence

## Why this exists

`Incident.evidence` is written once per occurrence — a new failing result on an
ACTIVE incident overwrites it, but an incident with no further failure (open,
acknowledged, or resolved) keeps its ORIGINAL snapshot forever. Every incident
that existed before the fix (`services/incident_evidence.py::_failing_result_layer`)
landed has a snapshot whose `observed_value` was never routed through the
column-policy/warehouse-tag masking floor — and `get_incident` (REST/MCP) and the
RCA narrative prompt both read that stored snapshot verbatim, never re-deriving
it. Deploying the code fix alone does nothing for those existing rows; this script
is the other half of closing #1772.

Delegates to `incident_service.redact_stale_evidence`, which is idempotent — safe
to re-run (a no-op the second time) and safe to run before OR after this fix's
code is live (an already-masked snapshot is left untouched either way). It
classifies each snapshot by the check's `(column, expectation_type)` AS OF the
incident's `last_seen_at` — the evidence write time — not the check's current row
(#1809), so a check edited after the incident opened is masked the way every live
surface would mask it, and a re-run after such an edit is still a no-op.
"""

from __future__ import annotations

from backend.app.db.session import get_session
from backend.app.services.incident_service import redact_stale_evidence


def main() -> int:
    session = get_session()
    try:
        updated = redact_stale_evidence(session)
    finally:
        session.close()
    print(f"redacted {updated} pre-fix incident evidence snapshot(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
