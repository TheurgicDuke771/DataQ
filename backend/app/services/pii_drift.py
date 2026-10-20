"""pii_drift monitor kind — ADR 0049 phase 2: "a column that looks like personal data just
appeared in a table nobody classified".

Stateful like schema_drift. Each run profiles every column of the suite's own target (one
login: list columns, then the batched top-values query), feeds the profiler classification
source as a full profile, and reads back the columns whose ONLY classification is a profiler
detection — no own warehouse tag, nothing inherited through lineage, not in the suite's
``pii_columns``, not dismissed. The first run records that set as the baseline and reports
``skip``; every later run reports the columns new against THAT baseline as ``metric_value``.
The baseline is fixed, exactly like schema_drift's: an unclassified column is a standing
condition, not an event, so the check keeps failing until someone classifies, dismisses or
ignores the column (which takes it out of the current set and lets the incident resolve) or
re-baselines the check. Rolling the baseline forward would pass the very next run and
auto-resolve the incident while the column is still unclassified.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore
from backend.app.datasources.base import CheckOutcome
from backend.app.datasources.monitors import PII_DRIFT, monitor_expectation_type, monitor_outcome
from backend.app.db.models import Asset, Check, Connection, Suite
from backend.app.services import profile_service
from backend.app.services.classification_vocab import DETECTED, fold_key
from backend.app.services.column_detections import detect
from backend.app.services.column_tags import effective_column_tags
from backend.app.services.coverage_settings_service import SuiteCoverage
from backend.app.services.failure_classifier import safe_failure_reason
from backend.app.services.monitor_baseline import get_baseline, insert_baseline_if_absent

log = get_logger(__name__)

#: Top values per column the run-time profile reads — the profiler's own default; the detectors'
#: ratios are computed over these.
PROFILE_TOP_N = 20
_FILE_TYPES = frozenset({"adls_gen2", "s3"})


class PiiDriftProfileError(Exception):
    """The target could not be profiled. Carries a CLASSIFIED message — safe for result rows."""


def _target_kwargs(
    connection: Connection, *, table: str, schema: str | None, catalog: str | None
) -> dict[str, Any]:
    """The profiler's location arguments for a run's resolved target: a flat-file target is a
    path, an Iceberg one an already-folded ``namespace.table`` identifier."""
    if connection.type in _FILE_TYPES:
        return {"path": table}
    if connection.type == "iceberg":
        return {"table": table}
    return {"table": table, "schema": schema, "catalog": catalog}


def profile_target(
    connection: Connection,
    *,
    session: Session,
    table: str,
    schema: str | None,
    catalog: str | None,
    secret_store: SecretStore,
    asset: Asset | None,
) -> profile_service.ProfileResult:
    """List → profile every column under one login; with ``asset`` the profile also feeds the
    classification source as a FULL profile (the stamp moves)."""
    location = _target_kwargs(connection, table=table, schema=schema, catalog=catalog)
    try:
        with profile_service.shared_connection():
            columns = profile_service.list_columns(
                connection, session=session, secret_store=secret_store, **location
            )
            return profile_service.profile_connection(
                connection,
                session=session,
                columns=columns,
                top_n=PROFILE_TOP_N,
                secret_store=secret_store,
                asset=asset,
                full_profile=asset is not None,
                **location,
            )
    except Exception as exc:
        log.warning(
            "pii_drift_profile_failed",
            connection_type=connection.type,
            error_type=type(exc).__name__,
        )
        raise PiiDriftProfileError(
            f"could not profile the target: {safe_failure_reason(exc)}"
        ) from exc


def policy_columns(policy: dict[str, Any] | None) -> set[str]:
    """The columns the suite's own policy has already classified — ``pii_columns`` — folded like
    the redaction map's keys.

    ``identifier_column`` is deliberately NOT here: it is a display decision, not a
    classification. The redaction ladder shows the identifier unmasked even over a detection, so
    an identifier that starts carrying email addresses is the one column that now leaks in
    failing samples — the monitor built to say so must not be the one place it is exempt.
    """
    if not policy:
        return set()
    return {fold_key(str(c)) for c in (policy.get("pii_columns") or []) if c}


def unclassified_detected_columns(
    tags: dict[str, str] | None,
    *,
    policy: dict[str, Any] | None,
    ignore_columns: list[str] | None = None,
) -> list[str]:
    """The columns whose only classification is a profiler detection.

    ``tags`` is `effective_column_tags`' map, so a column with an own warehouse verdict or one
    inherited through lineage carries that verdict, not ``detected``, and drops out here by
    construction; a dismissed detection is not in the map at all.
    """
    decided = policy_columns(policy) | {fold_key(c) for c in (ignore_columns or ())}
    return sorted(k for k, v in (tags or {}).items() if v == DETECTED and k not in decided)


def _with_in_memory_detections(tags: dict[str, str] | None, columns: list[Any]) -> dict[str, str]:
    """A dry run persists nothing: overlay this profile's detections the way a persisted run's
    would have been overlaid, under every stronger verdict already in the map."""
    merged = dict(tags or {})
    for d in detect(columns):
        merged.setdefault(fold_key(d.column), DETECTED)
    return merged


def build_pii_drift_executor(
    session: Session,
    *,
    connection: Connection,
    target_table: str,
    target_schema: str | None,
    target_catalog: str | None,
    secret_store: SecretStore,
    persist: bool = True,
    coverage: SuiteCoverage | None = None,  # the shared builder signature; unused here
) -> Callable[[Check], CheckOutcome]:
    """A per-run executor for pii_drift checks (the schema_drift pattern)."""
    from backend.app.services.run_service import resolve_asset  # the heavier module, lazily

    def errored(message: str) -> CheckOutcome:
        return CheckOutcome(
            expectation_type=monitor_expectation_type(PII_DRIFT),
            success=False,
            errored=True,
            error_message=message,
        )

    def executor(check: Check) -> CheckOutcome:
        suite = session.get(Suite, check.suite_id)
        asset = resolve_asset(session, suite) if suite is not None else None
        if suite is None or asset is None:
            return errored(
                "pii_drift needs the suite's asset to hold its classifications and none is "
                "resolved — set a run target on the suite"
            )
        try:
            result = profile_target(
                connection,
                session=session,
                table=target_table,
                schema=target_schema,
                catalog=target_catalog,
                secret_store=secret_store,
                asset=asset if persist else None,
            )
        except PiiDriftProfileError as exc:
            return errored(str(exc))
        config = dict(check.config)
        tags = effective_column_tags(session, asset)
        if not persist:
            tags = _with_in_memory_detections(tags, result.columns)
        current = unclassified_detected_columns(
            tags, policy=suite.column_policy, ignore_columns=config.get("ignore_columns")
        )
        baseline_row = get_baseline(session, check.id)
        payload: dict[str, Any] = {
            "columns_profiled": len(result.columns),
            "columns_detected": len(current),
        }
        if baseline_row is None:
            payload.update({"baseline_captured": True, "detected_columns": current})
            if persist:
                # Two first runs of one suite racing: the shared store's ON CONFLICT DO NOTHING.
                insert_baseline_if_absent(
                    session, check_id=check.id, kind=PII_DRIFT, baseline={"columns": current}
                )
            else:
                payload["dry_run"] = True
        else:
            # The baseline is never rewritten here (see the module docstring): the only way it
            # moves is the suite's re-baseline action deleting the row.
            known = {str(c) for c in baseline_row.baseline.get("columns", [])}
            payload.update(
                {
                    "new_columns": [c for c in current if c not in known],
                    "known_columns": sorted(known),
                    "baseline_captured_at": baseline_row.captured_at.isoformat(),
                }
            )
            if not persist:
                payload["dry_run"] = True
        return monitor_outcome(PII_DRIFT, scalar=payload, config=config, now=datetime.now(UTC))

    return executor
