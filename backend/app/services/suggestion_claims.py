"""The identity of a suggested rule, and claiming one for a check authored directly.

Apart from `suggestion_service` so `check_service` can import it: that module creates
checks through `check_service`.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import CheckSuggestion
from backend.app.services import audit_service


def fingerprint(expectation_type: str, config: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"type": expectation_type, "config": config}, sort_keys=True, default=str
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def claim_for_created_check(session: Session, check: Any, *, actor_id: uuid.UUID | None) -> bool:
    """A check was just authored directly (the Suggest-checks drawer's Add, the editor,
    the API) that is exactly a rule still pending in the suite's queue. Mark that rule
    accepted and point it at the check, so accepting it later cannot create a duplicate.
    Does not commit: it rides the caller's own commit, with the check.
    """
    if check.kind != "expectation":
        # A queued rule is always an expectation; a monitor that happens to share a type
        # and config with one is a different check.
        return False
    suggestion = session.scalar(
        select(CheckSuggestion)
        .where(
            CheckSuggestion.suite_id == check.suite_id,
            CheckSuggestion.fingerprint == fingerprint(check.expectation_type, check.config),
            CheckSuggestion.status == "pending",
        )
        .with_for_update()
    )
    if suggestion is None:
        return False
    suggestion.status = "accepted"
    suggestion.check_id = check.id
    suggestion.decided_by = actor_id
    suggestion.decided_at = datetime.now(UTC)
    # The same event the accept button writes: either way a person decided this rule.
    audit_service.record(
        session,
        action="suggestion.accept",
        entity_type="check_suggestion",
        entity_id=suggestion.id,
        actor=actor_id,
        after={
            "suite_id": str(suggestion.suite_id),
            "expectation_type": suggestion.expectation_type,
            "status": "accepted",
            "check_id": str(check.id),
        },
    )
    return True
