"""Dispatch for the *stateful* monitor kinds — one executor per `check.kind`.

`run_service` takes a single ``stateful_monitor_executor`` callable, because from
its point of view "a stateful kind needs the DB" is one fact, not N. But there
are now two such kinds — `schema_drift` (#592) and `anomaly` (#593) — with
genuinely different engines, so something has to route between them. That
something is here rather than inside the worker task, so the mapping lives next
to the kinds instead of inside a Celery entry point.

The mapping is explicit, and a stateful kind registered in
``MONITOR_KIND_REGISTRY`` with no builder here yields a per-check operational
``error`` (#122) rather than being quietly handed to the wrong engine — the
half-finished-kind failure mode the seam's own module comment warns about.
"""

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


# kind -> the builder for its per-run executor. Both builders share one signature
# on purpose: the run inputs a stateful kind can need (the session, the resolved
# target, the secret store) are the same for all of them, and keeping the shape
# uniform is what lets this stay a dict instead of a branch.
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
    """One callable covering every stateful kind, dispatching on ``check.kind``.

    Per-kind executors are built **lazily and cached** for the life of the run:
    building one is pure closure construction (no I/O), but a suite with only
    drift checks still shouldn't construct an anomaly engine, and a suite with
    twenty anomaly checks should construct one for all of them.
    """
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
