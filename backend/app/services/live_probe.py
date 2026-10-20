"""Disclosure seam for **live-probe** routes (#1419, #1479)."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import replace
from enum import StrEnum
from typing import Any

from sqlalchemy.orm import Session

from backend.app.services import audit_service
from backend.app.services.classification_vocab import DETECTED, fold_key

# The redaction ladder lives in `run_service` and is imported wholesale rather than partially re-
# implemented here.
from backend.app.services.run_service import (
    _REDACTED_VALUE,
    _SENSITIVE_TAG_VALUES,
    _known_sensitive,
    _may_show_incidental,
    _policy_requires_classification,
)

__all__ = [
    "Destination",
    "applicable_tags",
    "mask_profile_columns",
    "probe_is_own_target",
    "record_probe_access",
    "redact_probe_observed_value",
    "sensitive_profile_columns",
    "values_are_masked",
]


class Destination(StrEnum):
    """Where the probed values are going. See the module docstring."""

    #: A human's own authoring surface. Ephemeral, edit-gated, not persisted.
    INTERACTIVE = "interactive"
    #: The value outlives the moment or leaves the product — LLM context,
    #: downloaded file, alert payload, stored result.
    EGRESS = "egress"


def values_are_masked(policy: Mapping[str, Any] | None, *, destination: Destination) -> bool:
    """Whether this probe must mask cell values before returning them."""
    if destination is Destination.EGRESS:
        return True
    return _policy_requires_classification(policy)


def applicable_tags(
    tags: Mapping[str, str] | None, *, probed_other_target: bool
) -> Mapping[str, str] | None:
    """The asset's tags, filtered for a probe that may not be reading that asset."""
    if not tags:
        return tags
    if not probed_other_target:
        return tags
    # Only the MASKING verdicts survive: a clearance belongs to the asset it was given on, while
    # masking a same-named column on another table is the conservative direction — for a
    # warehouse `sensitive`, an inherited one and a profiler detection (ADR 0049) alike.
    return {
        column: value
        for column, value in tags.items()
        if str(value).strip().lower() in _SENSITIVE_TAG_VALUES
        or str(value).strip().lower() == DETECTED
    }


def _norm(value: Any) -> str | None:
    return None if value is None else fold_key(str(value))


def probe_is_own_target(
    suite: Any,
    connection: Any,
    *,
    table: str | None,
    schema: str | None,
    catalog: str | None,
    path: str | None,
    namespace: str | None,
) -> bool:
    """Whether an EXPLICIT probe location is the suite's own resolved target, field for field.

    Decides only whether a profile may feed the profiler classification source for the suite's
    asset (ADR 0049) — it never widens a clearance: `applicable_tags` keeps narrowing the tag
    floor for any explicit location, own target or not. Strict on purpose: an omitted schema or
    catalog resolves to the connection's default, which may be a different table of the same
    name, so it must match the target's value exactly (None to None). A batch target is a
    pattern with no concrete file, so it is never "own". A miss costs nothing but a detection.
    """
    target = getattr(suite, "target", None)
    if not target:
        return False
    from backend.app.services import run_target

    try:
        resolved = run_target.resolve_target(connection.type, target)
    except Exception:
        return False
    if resolved.batch is not None:
        return False
    if connection.type in ("adls_gen2", "s3"):
        return table is None and _norm(path) == _norm(resolved.table)
    if connection.type == "iceberg":
        # The resolver folds the namespace into `table`; compare against the raw target instead.
        return (
            path is None
            and _norm(table) == _norm(target.get("table"))
            and _norm(namespace) == _norm(target.get("namespace"))
        )
    return (
        path is None
        and _norm(table) == _norm(resolved.table)
        and _norm(schema) == _norm(resolved.schema)
        and _norm(catalog) == _norm(resolved.catalog)
    )


def redact_probe_observed_value(
    observed: dict[str, Any] | None,
    *,
    tested_column: str | None,
    policy: dict[str, Any] | None,
    tags: Mapping[str, str] | None = None,
    expectation_type: str | None = None,
) -> dict[str, Any] | None:
    """Thin call into `run_service.redact_observed_value`."""
    from backend.app.services.run_service import redact_observed_value

    return redact_observed_value(
        observed,
        tested_column=tested_column,
        expectation_type=expectation_type,
        policy=policy,
        tags=tags,
    )


def _profile_sample_values(column: Any) -> list[Any]:
    """The cell values a profile actually carries, for the name/value classifier."""
    values: list[Any] = [t.get("value") for t in (getattr(column, "top_values", None) or [])]
    for endpoint in (getattr(column, "min_value", None), getattr(column, "max_value", None)):
        if endpoint is not None:
            values.append(endpoint)
    return values


def sensitive_profile_columns(
    columns: Sequence[Any],
    *,
    policy: Mapping[str, Any] | None,
    tags: Mapping[str, str] | None = None,
    destination: Destination = Destination.INTERACTIVE,
) -> list[str]:
    """Names of the profiled columns the redaction ladder considers sensitive."""

    # The rung differs by destination, which is the same principle one level deeper rather than a
    # new one.
    if destination is Destination.EGRESS:
        return [
            column.column
            for column in columns
            if not _may_show_incidental(column.column, _profile_sample_values(column), policy, tags)
        ]
    return [
        column.column
        for column in columns
        if _known_sensitive(column.column, _profile_sample_values(column), policy, tags)
    ]


def mask_profile_columns(columns: Sequence[Any], *, sensitive: Sequence[str]) -> list[Any]:
    """Blank cell *values* on sensitive columns, keeping every statistic."""
    if not sensitive:
        return list(columns)
    blocked = set(sensitive)
    out: list[Any] = []
    for column in columns:
        if column.column not in blocked:
            out.append(column)
            continue
        out.append(
            replace(
                column,
                min_value=None,
                max_value=None,
                top_values=[
                    {"value": _REDACTED_VALUE, "count": t.get("count")}
                    for t in (column.top_values or [])
                ],
            )
        )
    return out


def record_probe_access(
    session: Session,
    *,
    action: str,
    suite_id: uuid.UUID,
    actor: Any | None,
    destination: Destination,
    masked: bool,
    values_in_scope: bool = True,
    columns: Sequence[str] | None = None,
    sensitive_columns: Sequence[str] | None = None,
    detail: dict[str, Any] | None = None,
    actor_kind: str = "user",
) -> None:
    """Record that a live probe disclosed (or masked) live warehouse data."""
    payload: dict[str, Any] = {"destination": destination.value, "masked": masked}
    if not values_in_scope:
        # A probe that returns only column NAMES never had cell values to mask.
        payload["masked"] = False
        payload["values_in_scope"] = False
    if columns:
        payload["columns"] = list(columns)
    if sensitive_columns:
        payload["sensitive_columns"] = list(sensitive_columns)
    if detail:
        payload.update(detail)

    audit_service.record_access(
        session,
        action=action,
        entity_type="suite",
        entity_id=suite_id,
        actor=actor,
        # Masked probes are recorded with `exposed=False` rather than dropped: "this person profiled
        # the customers table and saw nothing" is a different fact from "nobody profiled it".
        exposed=masked is False and values_in_scope,
        detail=payload,
        actor_kind=actor_kind,
    )
