"""Shared LLM-prompt context assembly (ADR 0042): column listing + masked
profile stats, reused by every feature kind that needs a table's schema/data
shape as prompt context (#1512 SQL generation, #1513 check suggestions).
Centralizes the G3 governance-floor application (warehouse tags + the
suite's own `column_policy` -> masking) so a future fix to that logic takes
effect for every LLM feature at once, not just the one it was written in.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore, SecretStoreUnavailableError
from backend.app.db.models import Connection, Suite
from backend.app.llm.base import LLMRequestInvalidError
from backend.app.services import profile_service, run_service
from backend.app.services.custom_sql import SQL_QUERYABLE_TYPES
from backend.app.services.live_probe import (
    Destination,
    applicable_tags,
    mask_profile_columns,
    record_probe_access,
    sensitive_profile_columns,
)
from backend.app.services.profile_service import ColumnProfile

log = get_logger(__name__)


def _clean(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def target_identity(suite: Suite) -> dict[str, Any]:
    target = suite.target or {}
    return {
        "table": target.get("table"),
        "schema": target.get("schema"),
        "catalog": target.get("catalog"),
        "namespace": target.get("namespace"),
    }


def qualify(catalog: str | None, schema: str | None, table: str | None) -> str:
    """A dotted `catalog.schema.table` identifier, skipping any part that's `None`."""
    return ".".join(p for p in (catalog, schema, table) if p)


def qualified_target(suite: Suite) -> str:
    target = suite.target or {}
    return qualify(
        _clean(target.get("catalog")), _clean(target.get("schema")), _clean(target.get("table"))
    )


def check_sql_target_preconditions(
    suite: Suite,
    connection: Connection | None,
    *,
    datasource_message: str,
    no_target_message: str,
) -> None:
    """Refuses (`LLMRequestInvalidError`) unless `suite` targets a SQL-queryable
    connection with a real table (+ catalog for Unity Catalog). Shared by every
    SQL-scoped LLM feature kind's route (a synchronous 422) and its builder (the
    TOCTOU re-check), so a precondition cannot be added at one altitude — or one
    feature kind — and missed at the others.
    """
    if connection is None or connection.type not in SQL_QUERYABLE_TYPES:
        raise LLMRequestInvalidError(
            datasource_message, detail={"supported": sorted(SQL_QUERYABLE_TYPES)}
        )
    target = suite.target or {}
    if _clean(target.get("table")) is None:
        raise LLMRequestInvalidError(no_target_message)
    # The run path requires a catalog for Unity Catalog; drifting from it would
    # generate/profile against a name the actual run would refuse.
    if connection.type == "unity_catalog" and _clean(target.get("catalog")) is None:
        raise LLMRequestInvalidError("a Unity Catalog target requires a catalog")


def list_columns_for_target(
    session: Session,
    suite: Suite,
    connection: Connection,
    *,
    table: str | None,
    schema: str | None = None,
    catalog: str | None = None,
    namespace: str | None = None,
    secret_store: SecretStore,
    actor: uuid.UUID | None,
    consumer: str,
) -> list[str]:
    """Column names for an LLM prompt (name-only EGRESS), against an EXPLICIT
    target — not necessarily the suite's own resolved one (#1649's additional
    same-connection tables). Refuses (rather than degrades) on failure: a
    prompt grounded in guessed columns is a confident wrong answer, and a
    secret-store outage is an outage, never a prompt state (ADR 0039).
    """
    try:
        columns = profile_service.list_columns(
            connection,
            session=session,
            secret_store=secret_store,
            table=table,
            schema=schema,
            catalog=catalog,
            namespace=namespace,
        )
    except SecretStoreUnavailableError:
        raise
    except Exception as exc:
        log.warning(
            "llm_prompt_columns_unavailable",
            suite_id=str(suite.id),
            consumer=consumer,
            table=table,
            error=exc.__class__.__name__,
        )
        raise LLMRequestInvalidError(
            f"the table's columns could not be read (table={table!r}) — check the connection "
            "credential and the target"
        ) from exc
    # Name-only egress is still egress: the default path must leave a record too.
    record_probe_access(
        session,
        action="column.list",
        suite_id=suite.id,
        actor=actor,
        destination=Destination.EGRESS,
        masked=False,
        values_in_scope=False,
        columns=columns,
        detail={"consumer": consumer, "table": table, "schema": schema, "catalog": catalog},
    )
    return columns


def list_columns_for_prompt(
    session: Session,
    suite: Suite,
    connection: Connection,
    *,
    secret_store: SecretStore,
    actor: uuid.UUID | None,
    consumer: str,
) -> list[str]:
    """`list_columns_for_target` against the suite's OWN resolved target."""
    return list_columns_for_target(
        session,
        suite,
        connection,
        secret_store=secret_store,
        actor=actor,
        consumer=consumer,
        **target_identity(suite),
    )


@dataclass(frozen=True)
class MaskedProfile:
    row_count: int
    columns: list[ColumnProfile]


def _policy_for_target(
    policy: dict[str, Any] | None, *, probed_other_target: bool
) -> dict[str, Any] | None:
    """`suite.column_policy`, narrowed for a probe against a target that ISN'T
    the suite's own — see `masked_profile_for_target`'s docstring for why
    `identifier_column` (but not `pii_columns`) is dropped.
    """
    if not probed_other_target or not policy or "identifier_column" not in policy:
        return policy
    return {k: v for k, v in policy.items() if k != "identifier_column"}


def masked_profile_for_target(
    session: Session,
    suite: Suite,
    connection: Connection,
    columns: list[str],
    *,
    top_n: int,
    table: str | None,
    schema: str | None = None,
    catalog: str | None = None,
    namespace: str | None = None,
    probed_other_target: bool,
    secret_store: SecretStore,
    actor: uuid.UUID | None,
    consumer: str,
) -> MaskedProfile:
    """Column profile stats for an LLM prompt, masked per the G3 governance
    floor (warehouse tags joined with the suite's own policy). Refuses on
    failure — a caller that wants the profile to stay optional (#1512's shape)
    should catch `LLMRequestInvalidError` and degrade itself.

    `probed_other_target` must be `True` whenever `table`/`schema`/`catalog`
    isn't the suite's own resolved target (#1649's additional same-connection
    tables): the warehouse tags looked up below are the SUITE's asset's own,
    and `applicable_tags` narrows them to only the values that are sensitive
    on ANY asset — an unrelated table's column sharing a name with a
    known-sensitive one must not be waved through as "confirmed non-PII"
    just because the tag lookup targeted a different table entirely. The
    suite's `column_policy` gets the SAME narrowing (#1649 review):
    `identifier_column` is a per-asset "this is the safe shown identifier"
    clearance that means nothing on an unrelated table — nearly every table
    has a column literally named `id`, and a bare name match there must not
    silently satisfy the G3 fail-closed floor for a column never actually
    reviewed in THAT table's context. `pii_columns` is kept: masking an
    unrelated table's column because its name matches a known-PII name is
    the conservative (fail-SAFE) direction, unlike a clearance.
    """
    try:
        profile = profile_service.profile_connection(
            connection,
            session=session,
            columns=columns,
            top_n=top_n,
            secret_store=secret_store,
            table=table,
            schema=schema,
            catalog=catalog,
            namespace=namespace,
        )
    except SecretStoreUnavailableError:
        raise
    except Exception as exc:
        log.warning(
            "llm_prompt_profile_unavailable",
            suite_id=str(suite.id),
            consumer=consumer,
            table=table,
            error=exc.__class__.__name__,
        )
        raise LLMRequestInvalidError(
            f"the table could not be profiled (table={table!r}) — check the connection "
            "credential and the target"
        ) from exc
    tags = applicable_tags(
        run_service.asset_column_tags(session, suite), probed_other_target=probed_other_target
    )
    policy = _policy_for_target(suite.column_policy, probed_other_target=probed_other_target)
    sensitive = sensitive_profile_columns(
        profile.columns, policy=policy, tags=tags, destination=Destination.EGRESS
    )
    masked = mask_profile_columns(profile.columns, sensitive=sensitive)
    record_probe_access(
        session,
        action="column.profile",
        suite_id=suite.id,
        actor=actor,
        destination=Destination.EGRESS,
        masked=True,
        columns=columns,
        sensitive_columns=sensitive,
        detail={"consumer": consumer, "table": table, "schema": schema, "catalog": catalog},
    )
    return MaskedProfile(row_count=profile.row_count, columns=masked)


def masked_profile_for_prompt(
    session: Session,
    suite: Suite,
    connection: Connection,
    columns: list[str],
    *,
    top_n: int,
    secret_store: SecretStore,
    actor: uuid.UUID | None,
    consumer: str,
) -> MaskedProfile:
    """`masked_profile_for_target` against the suite's OWN resolved target."""
    return masked_profile_for_target(
        session,
        suite,
        connection,
        columns,
        top_n=top_n,
        probed_other_target=False,
        secret_store=secret_store,
        actor=actor,
        consumer=consumer,
        **target_identity(suite),
    )
