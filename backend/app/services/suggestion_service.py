"""The automatic-coverage review queue (ADR 0047 §4).

Rules that assert something about a covered table's data are proposed here, never created
directly: a person accepts one (it becomes a check in the suite, ``origin='suggestion'``) or
rejects it. ``(suite_id, fingerprint)`` is unique, so a rule is proposed at most once per suite —
a rejection is remembered, and an accepted rule whose check is later deleted is not proposed
again either.

Profiling is the deterministic source: a column never null in a meaningful sample, an id-like
column that is fully distinct, a low-cardinality column's value set. Columns the suite's policy
or the warehouse classifies as sensitive are never proposed as a value set, since that would copy
their values into the check.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore
from backend.app.db.models import Asset, CheckSuggestion, Connection, Suite
from backend.app.services import (
    audit_service,
    check_service,
    column_tags,
    profile_service,
    run_target,
)
from backend.app.services.suite_authz import require_permission

log = get_logger(__name__)

#: Fewer rows than this is too little evidence to propose that a column is never null or unique.
MIN_ROWS = 100
#: Largest value set proposed as accepted values.
MAX_ACCEPTED_VALUES = 10
_TOP_N = MAX_ACCEPTED_VALUES + 1
_ID_LIKE = re.compile(r"(^id$|_id$|^uuid$|_uuid$|_key$)", re.IGNORECASE)


class SuggestionNotFoundError(DataQError):
    status_code = 404
    code = "suggestion_not_found"


class SuggestionDecidedError(DataQError):
    status_code = 409
    code = "suggestion_already_decided"


def fingerprint(expectation_type: str, config: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"type": expectation_type, "config": config}, sort_keys=True, default=str
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def propose(
    session: Session,
    suite: Suite,
    *,
    source: str,
    name: str,
    expectation_type: str,
    config: dict[str, Any],
    rationale: str | None,
) -> bool:
    """Queue a rule unless this suite has seen it before; ``True`` if it was new."""
    inserted = session.scalar(
        pg_insert(CheckSuggestion)
        .values(
            suite_id=suite.id,
            source=source,
            fingerprint=fingerprint(expectation_type, config),
            name=name[:256],
            expectation_type=expectation_type,
            config=config,
            rationale=rationale,
        )
        .on_conflict_do_nothing(constraint="uq_check_suggestions_rule")
        .returning(CheckSuggestion.id)
    )
    return inserted is not None


def _value_set_is_sensitive(session: Session, suite: Suite) -> Callable[[str, list[Any]], bool]:
    """The redaction ladder's own test for the TESTED column (``run_service._known_sensitive``):
    a governance tag (own or inherited through lineage), the suite's policy, fail-closed mode,
    or an affirmative name/value PII signal. A value set shown in a check is held to the same
    bar as a failing sample of that column."""
    from backend.app.services.run_service import _known_sensitive

    asset = session.get(Asset, suite.asset_id) if suite.asset_id else None
    tags = column_tags.effective_column_tags(session, asset)
    policy = suite.column_policy

    def sensitive(column: str, values: list[Any]) -> bool:
        return _known_sensitive(column, values, policy, tags)

    return sensitive


def rules_from_profile(
    profile: profile_service.ProfileResult, *, sensitive: Callable[[str, list[Any]], bool]
) -> list[dict[str, Any]]:
    """Deterministic proposals from one profile of the table."""
    rows = profile.row_count
    if rows < MIN_ROWS:
        return []
    rules: list[dict[str, Any]] = []
    for col in profile.columns:
        name = col.column
        if col.null_count == 0:
            rules.append(
                {
                    "name": f"{name} is never null",
                    "expectation_type": "expect_column_values_to_not_be_null",
                    "config": {"column": name},
                    "rationale": f"No nulls in {rows:,} rows.",
                }
            )
        non_null = rows - col.null_count
        if col.distinct_count is not None and col.distinct_count == non_null > 0:
            if _ID_LIKE.search(name):
                rules.append(
                    {
                        "name": f"{name} is unique",
                        "expectation_type": "expect_column_values_to_be_unique",
                        "config": {"column": name},
                        "rationale": f"Every one of {non_null:,} values is distinct.",
                    }
                )
        elif (
            col.distinct_count is not None
            and 2 <= col.distinct_count <= MAX_ACCEPTED_VALUES
            and len(col.top_values) == col.distinct_count
        ):
            values = sorted(
                (v["value"] for v in col.top_values if v.get("value") is not None), key=str
            )
            if (
                values
                and not sensitive(name, values)
                and all(isinstance(v, (str, int)) and not isinstance(v, bool) for v in values)
            ):
                rules.append(
                    {
                        "name": f"{name} is one of {len(values)} known values",
                        "expectation_type": "expect_column_values_to_be_in_set",
                        "config": {"column": name, "value_set": values},
                        "rationale": f"Only these {len(values)} values in {rows:,} rows.",
                    }
                )
    return rules


def _classify_before_proposing(
    session: Session, suite: Suite, connection: Connection, *, secret_store: SecretStore
) -> None:
    """Read the warehouse's column tags and derive the suite's redaction policy first. A new
    automatic suite has neither — tags are cached by a run, the policy by the REST create path —
    and a value-set proposal must not be judged against an empty classification."""
    from backend.app.worker.tasks import _auto_classify_columns

    column_tags.refresh_asset_column_tags(
        session,
        suite=suite,
        connection=connection,
        target=run_target.resolve_target(connection.type, suite.target),
        secret_store=secret_store,
    )
    _auto_classify_columns(session, suite_id=suite.id)
    session.refresh(suite)


def refresh_from_profile(session: Session, suite: Suite, *, secret_store: SecretStore) -> int:
    """Profile the suite's table once and queue what it supports; returns how many were new."""
    connection = session.get(Connection, suite.connection_id)
    target = suite.target or {}
    if connection is None or not target.get("table"):
        return 0
    _classify_before_proposing(session, suite, connection, secret_store=secret_store)
    profile = profile_service.profile_connection(
        connection,
        session=session,
        columns=profile_service.list_columns(
            connection,
            session=session,
            table=target["table"],
            schema=target.get("schema"),
            catalog=target.get("catalog"),
            secret_store=secret_store,
        ),
        top_n=_TOP_N,
        table=target["table"],
        schema=target.get("schema"),
        catalog=target.get("catalog"),
        secret_store=secret_store,
    )
    created = sum(
        propose(session, suite, source="profile", **rule)
        for rule in rules_from_profile(profile, sensitive=_value_set_is_sensitive(session, suite))
    )
    state = dict(suite.auto_state or {})
    state["profiled_at"] = datetime.now(UTC).isoformat()
    suite.auto_state = state
    session.commit()
    return created


def list_suggestions(
    session: Session, suite_id: uuid.UUID, *, user_id: uuid.UUID, status: str | None = "pending"
) -> list[CheckSuggestion]:
    require_permission(session, suite_id, user_id, minimum="view")
    query = select(CheckSuggestion).where(CheckSuggestion.suite_id == suite_id)
    if status is not None:
        query = query.where(CheckSuggestion.status == status)
    return list(session.scalars(query.order_by(CheckSuggestion.created_at, CheckSuggestion.name)))


def _pending(session: Session, suggestion_id: uuid.UUID, user_id: uuid.UUID) -> CheckSuggestion:
    suggestion = session.get(CheckSuggestion, suggestion_id, with_for_update=True)
    if suggestion is None:
        raise SuggestionNotFoundError("suggestion not found")
    require_permission(session, suggestion.suite_id, user_id, minimum="edit")
    if suggestion.status != "pending":
        raise SuggestionDecidedError(
            f"this suggestion was already {suggestion.status}",
            detail={"status": suggestion.status},
        )
    return suggestion


def accept(session: Session, suggestion_id: uuid.UUID, *, user_id: uuid.UUID) -> CheckSuggestion:
    """Create the proposed check (through the same validation as any check) and record it.

    The suggestion is claimed under its row lock BEFORE the check is created, so the claim and
    the check commit together (``create_check`` commits): a concurrent accept waiting on the lock
    then sees ``accepted`` and gets 409 instead of creating a second check.
    """
    suggestion = _pending(session, suggestion_id, user_id)
    suggestion.status = "accepted"
    suggestion.decided_by = user_id
    suggestion.decided_at = datetime.now(UTC)
    try:
        check = check_service.create_check(
            session,
            suite_id=suggestion.suite_id,
            name=suggestion.name,
            kind="expectation",
            expectation_type=suggestion.expectation_type,
            config=dict(suggestion.config),
            warn_threshold=None,
            fail_threshold=None,
            critical_threshold=None,
            actor_id=user_id,
            origin="suggestion",
        )
    except Exception:
        session.rollback()  # the claim goes with the failed create
        raise
    decided = session.get(CheckSuggestion, suggestion_id)
    assert decided is not None  # nosec B101 — claimed above, committed with the check
    _decide(session, decided, status="accepted", user_id=user_id, check_id=check.id)
    return decided


def reject(session: Session, suggestion_id: uuid.UUID, *, user_id: uuid.UUID) -> CheckSuggestion:
    suggestion = _pending(session, suggestion_id, user_id)
    _decide(session, suggestion, status="rejected", user_id=user_id, check_id=None)
    return suggestion


def _decide(
    session: Session,
    suggestion: CheckSuggestion,
    *,
    status: str,
    user_id: uuid.UUID,
    check_id: uuid.UUID | None,
) -> None:
    suggestion.status = status
    suggestion.check_id = check_id
    suggestion.decided_by = user_id
    suggestion.decided_at = datetime.now(UTC)
    audit_service.record(
        session,
        action=f"suggestion.{status.removesuffix('ed')}",
        entity_type="check_suggestion",
        entity_id=suggestion.id,
        actor=user_id,
        after={
            "suite_id": str(suggestion.suite_id),
            "expectation_type": suggestion.expectation_type,
            "status": status,
            "check_id": str(check_id) if check_id else None,
        },
    )
    session.commit()
    log.info(
        "suggestion_decided",
        suggestion_id=str(suggestion.id),
        suite_id=str(suggestion.suite_id),
        status=status,
    )
