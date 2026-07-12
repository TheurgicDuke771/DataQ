"""Execute comparison checks inside a suite run (ADR 0015, #794).

`build_comparison_executor` closes over the run's already-resolved target side
(the suite's connection + materialized table/path) and returns a callable the
run path invokes per `comparison` check: read both sides through the #792
`DatasetReader` (source = the check's `source_connection_id` + `config.source`;
target = the suite side, optionally projected by `config.target_query`), diff
them with the #793 engine, and map the buckets onto a `CheckOutcome`.

Failure semantics (#122): everything that prevents *evaluating* the diff — an
unreadable side, an over-cap dataset, duplicate/NULL keys, a deleted source
connection — is an operational ``error`` outcome, never a data-quality ``fail``
and never a raised exception (one broken comparison must not fail its siblings'
run). `DataQError` messages are ours and redaction-safe, so they surface
verbatim; unexpected exceptions surface only their `classify_failure_reason`
category (raw text can carry DSN/credential fragments) and are logged
server-side.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from backend.app.core.errors import DataQError
from backend.app.core.logging import get_logger
from backend.app.core.secrets import SecretStore
from backend.app.datasources.base import CheckOutcome
from backend.app.datasources.comparison import (
    ColumnComparisonResult,
    compare_columns,
    compare_records,
    parse_tolerance,
)
from backend.app.datasources.flatfile import BatchNotFoundError
from backend.app.db.models import COMPARISON_KIND, Check, Connection
from backend.app.services import run_target
from backend.app.services.dataset_reader import DatasetSpec, default_max_rows, read_dataset
from backend.app.services.failure_classifier import classify_failure_reason

log = get_logger(__name__)

ComparisonExecutor = Callable[[Check], CheckOutcome]


def _error_outcome(check: Check, message: str) -> CheckOutcome:
    return CheckOutcome(
        expectation_type=check.expectation_type,
        success=False,
        errored=True,
        error_message=message,
    )


def _source_spec(
    source_conn: Connection, source_cfg: dict[str, Any], *, secret_store: SecretStore
) -> DatasetSpec:
    """The check's `config.source` → a readable `DatasetSpec`.

    A `query` projection is passed through (validated read-only at author time
    and re-validated by the reader). A dataset spec goes through the same
    `resolve_target` + batch materialization a suite target does, so flat-file
    batch sources resolve to a concrete object exactly like a run's own target.
    """
    if "query" in source_cfg:
        return DatasetSpec(query=source_cfg["query"])
    resolved = run_target.resolve_target(source_conn.type, source_cfg)
    table = run_target.materialize_path(
        source_conn.type,
        source_conn.config,
        resolved,
        secret_ref=source_conn.secret_ref,
        secret_store=secret_store,
    )
    return DatasetSpec(table=table, schema=resolved.schema, catalog=resolved.catalog)


def _observed(result: Any) -> dict[str, Any]:
    """Bucket counts per grain — the shared identity fields plus either the
    row-grain or the value-grain (#799) counters."""
    base = {
        "source_rows": result.source_rows,
        "target_rows": result.target_rows,
        "mismatch_percent": result.mismatch_percent,
        "columns_compared": result.columns_compared,
        "columns_only_in_source": result.columns_only_in_source,
        "columns_only_in_target": result.columns_only_in_target,
    }
    if isinstance(result, ColumnComparisonResult):
        return {
            **base,
            "matched_values": result.matched_values,
            "mismatched_values": result.mismatched_values,
            "additional_in_source_values": result.additional_in_source_values,
            "additional_in_target_values": result.additional_in_target_values,
            "per_column": result.per_column,
        }
    return {
        **base,
        "matched": result.matched,
        "mismatched": result.mismatched,
        "additional_in_source": result.additional_in_source,
        "additional_in_target": result.additional_in_target,
        "column_mismatch_counts": result.column_mismatch_counts,
    }


def build_comparison_executor(
    session: Session,
    *,
    suite_connection: Connection,
    target_table: str,
    target_schema: str | None,
    target_catalog: str | None,
    secret_store: SecretStore,
) -> ComparisonExecutor:
    """An executor bound to this run's resolved target side.

    ``target_table`` is the run's materialized table/path — the same value the
    GX runner receives, so both check kinds validate the identical dataset.
    """

    def execute(check: Check) -> CheckOutcome:
        cfg = dict(check.config)
        try:
            source_conn = (
                session.get(Connection, check.source_connection_id)
                if check.source_connection_id
                else None
            )
            if source_conn is None:
                # RESTRICT makes this near-impossible; belt for torn state.
                return _error_outcome(check, "comparison source connection not found")
            max_rows = int(cfg.get("max_rows") or default_max_rows())

            src_spec = _source_spec(
                source_conn, dict(cfg.get("source") or {}), secret_store=secret_store
            )
            if cfg.get("target_query"):
                tgt_spec = DatasetSpec(query=cfg["target_query"])
            else:
                tgt_spec = DatasetSpec(
                    table=target_table, schema=target_schema, catalog=target_catalog
                )

            source_df = read_dataset(
                source_conn, src_spec, max_rows=max_rows, secret_store=secret_store
            )
            target_df = read_dataset(
                suite_connection, tgt_spec, max_rows=max_rows, secret_store=secret_store
            )
            # Grain dispatch (#799): `comparison:columns` = FDC's per-column
            # value grain; anything else (the canonical `comparison:records`)
            # = row grain. Tolerance applies to both.
            engine = (
                compare_columns
                if check.expectation_type == "comparison:columns"
                else compare_records
            )
            result = engine(
                source_df,
                target_df,
                keys=list(cfg.get("keys") or []),
                columns=cfg.get("columns"),
                tolerance=parse_tolerance(cfg.get("tolerance")),
            )
        except BatchNotFoundError:
            # A routine "baseline hasn't landed yet" on a flat-file batch
            # source — friendly and specific, not the generic UNKNOWN failure
            # (the suite's own target gets a whole-run skip for this; a missing
            # SOURCE batch is per-check, so it surfaces as this error result).
            return _error_outcome(
                check,
                "comparison source batch not found — no file matched the source "
                "pattern (the baseline data may not have landed yet)",
            )
        except DataQError as exc:
            # Our own typed refusals (over-cap, duplicate/NULL keys, unreadable
            # side) — messages are redaction-safe by construction.
            return _error_outcome(check, exc.message)
        except Exception as exc:
            log.exception(
                "comparison_check_failed",
                check_id=str(check.id),
                source_connection_id=str(check.source_connection_id),
            )
            return _error_outcome(check, classify_failure_reason(exc))

        samples: dict[str, Any] = {}
        if result.sample_mismatched:
            samples["mismatched"] = result.sample_mismatched
        if result.sample_additional_in_source:
            samples["additional_in_source"] = result.sample_additional_in_source
        if result.sample_additional_in_target:
            samples["additional_in_target"] = result.sample_additional_in_target

        return CheckOutcome(
            expectation_type=check.expectation_type,
            success=result.success,
            # The badness scalar (ADR 0016 banding + metric_value trends).
            metric_value=result.mismatch_percent,
            observed_value=_observed(result),
            expected_value={
                "source_connection_id": str(check.source_connection_id),
                "source": cfg.get("source"),
                "keys": cfg.get("keys"),
                "columns": cfg.get("columns"),
                "max_rows": max_rows,
                "tolerance": cfg.get("tolerance"),
            },
            sample_failures=samples or None,
        )

    return execute


def has_comparison_checks(checks: list[Check]) -> bool:
    """Whether the run needs a comparison executor at all (cheap pre-check so
    non-comparison suites build nothing extra)."""
    return any(c.kind == COMPARISON_KIND for c in checks)
