"""Dispatch for the *stateful* monitor kinds — one executor per `check.kind`."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from sqlalchemy.orm import Session

from backend.app.core.secrets import SecretStore
from backend.app.datasources.base import CheckOutcome
from backend.app.datasources.monitors import ANOMALY, SCHEMA_DRIFT
from backend.app.db.models import Check, Connection
from backend.app.services import anomaly, schema_drift


class _ExecutorBuilder(Protocol):
    def __call__(
        self,
        session: Session,
        *,
        connection: Connection,
        target_table: str,
        target_schema: str | None,
        target_catalog: str | None,
        secret_store: SecretStore,
        persist: bool = True,
    ) -> Callable[[Check], CheckOutcome]: ...


# kind -> the builder for its per-run executor.
_BUILDERS: dict[str, _ExecutorBuilder] = {
    SCHEMA_DRIFT: schema_drift.build_schema_drift_executor,
    ANOMALY: anomaly.build_anomaly_executor,
}


def build_stateful_monitor_executor(
    session: Session,
    *,
    connection: Connection,
    target_table: str,
    target_schema: str | None,
    target_catalog: str | None,
    secret_store: SecretStore,
    persist: bool = True,
) -> Callable[[Check], CheckOutcome]:
    """One callable covering every stateful kind, dispatching on ``check.kind``."""
    built: dict[str, Callable[[Check], CheckOutcome]] = {}

    def executor(check: Check) -> CheckOutcome:
        builder = _BUILDERS.get(check.kind)
        if builder is None:
            return CheckOutcome(
                expectation_type=check.expectation_type,
                success=False,
                errored=True,
                error_message=(
                    f"no stateful monitor engine is wired for kind {check.kind!r} — "
                    "the kind is registered but has no executor"
                ),
            )
        cached = built.get(check.kind)
        if cached is None:
            cached = builder(
                session,
                connection=connection,
                target_table=target_table,
                target_schema=target_schema,
                target_catalog=target_catalog,
                secret_store=secret_store,
                persist=persist,
            )
            built[check.kind] = cached
        return cached(check)

    return executor
