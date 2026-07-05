"""DataQ ↔ dbt build callback snippet (run after `dbt build`).

dbt Core has no callback context (unlike Airflow's `on_*_callback`), so this is a
small **post-build wrapper**: run it right after `dbt build`, pointed at the run's
``run_results.json``, and it POSTs a compact, HMAC-signed JSON document to DataQ's
dbt event receiver. DataQ records the run in ``pipeline_runs`` and, on success,
triggers any suite bound to this job (DataQ ADR 0004 / 0029). Both success and
failure are reported; only success fires a trigger.

Wire it into your build wrapper (e.g. the container entrypoint)::

    dbt build
    python dataq_dbt_callback.py target/run_results.json   # never fails the build

Configuration — environment variables, read at call time:

    DATAQ_WEBHOOK_URL     Full receiver URL, e.g.
                          https://dataq.example.com/api/v1/orchestration/events/dbt
    DATAQ_WEBHOOK_SECRET  HMAC signing key — the SAME value DataQ stores in Key
                          Vault as ``dbt-webhook-secret``.
    DATAQ_DBT_PROJECT     Project name. MUST match the ``project_name`` of the dbt
                          connection registered in DataQ (that is how DataQ
                          attributes the run). Falls back to the project parsed
                          from ``run_results.json`` node ids when unset.
    DATAQ_DBT_JOB         Job name — the trigger unit (``pipeline_or_dag_id``). One
                          project may expose several jobs (e.g. distinct --select
                          slices); bind a suite to a specific job. Required.

Design notes: stdlib-only (no extra pip installs), and **fail-safe** — every error
is swallowed and logged, so a callback failure can never break your pipeline. The
HMAC is computed over the exact bytes that are POSTed, matching DataQ's
constant-time check on the raw request body (identical scheme to the Airflow
snippet).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from typing import Any

_LOG = logging.getLogger(__name__)

_SIGNATURE_HEADER = "X-DataQ-Signature"
_TIMEOUT_SECONDS = 10
_DEFAULT_RESULTS_PATH = "target/run_results.json"

# dbt node result statuses that mean the build failed (see DataQ orchestration/dbt.py).
_FAILURE_STATUSES = frozenset({"error", "fail", "runtime error"})


def status_from_results(results: list[dict[str, Any]]) -> str:
    """'failed' if any node failed, else 'succeeded' (matches DataQ ADR 0029)."""
    for node in results:
        if str(node.get("status", "")).lower() in _FAILURE_STATUSES:
            return "failed"
    return "succeeded"


def _project_from_nodes(results: list[dict[str, Any]]) -> str | None:
    """dbt node ids are ``<resource_type>.<project>.<name>`` — pull the project."""
    for node in results:
        parts = str(node.get("unique_id", "")).split(".")
        if len(parts) >= 3:
            return parts[1]
    return None


def build_payload(
    *,
    project_name: str,
    job_name: str,
    invocation_id: str,
    status: str,
    started_at: str | None = None,
    finished_at: str | None = None,
    error: str | None = None,
) -> bytes:
    """Serialise the event to the exact bytes that are signed and POSTed.

    Compact JSON, so the bytes are stable: the signature is computed over this
    return value and the same value is sent as the request body — never re-encoded
    in between (which would invalidate the signature).
    """
    doc: dict[str, Any] = {
        "project_name": project_name,
        "job_name": job_name,
        "invocation_id": invocation_id,
        "status": status,
    }
    if started_at:
        doc["started_at"] = started_at
    if finished_at:
        doc["finished_at"] = finished_at
    if error:
        doc["error"] = error
    return json.dumps(doc, separators=(",", ":")).encode("utf-8")


def sign(secret: str, body: bytes) -> str:
    """HMAC-SHA256 hex digest over the raw body — matches DataQ's receiver."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _post(url: str, body: bytes, signature: str) -> int:
    request = urllib.request.Request(  # noqa: S310 — url is operator-configured, not user input
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", _SIGNATURE_HEADER: signature},
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310
        return int(response.status)


def notify(results_path: str = _DEFAULT_RESULTS_PATH) -> None:
    """Read run_results.json, build+sign+POST one event. Never raises."""
    url = os.environ.get("DATAQ_WEBHOOK_URL")
    secret = os.environ.get("DATAQ_WEBHOOK_SECRET")
    job_name = os.environ.get("DATAQ_DBT_JOB")
    if not url or not secret:
        _LOG.warning("DataQ dbt callback skipped: DATAQ_WEBHOOK_URL / DATAQ_WEBHOOK_SECRET not set")
        return
    if not job_name:
        _LOG.warning("DataQ dbt callback skipped: DATAQ_DBT_JOB not set")
        return

    try:
        with open(results_path, "rb") as fh:
            doc = json.load(fh)
        metadata = doc["metadata"]
        results = doc.get("results", [])
        project_name = os.environ.get("DATAQ_DBT_PROJECT") or _project_from_nodes(results)
        if not project_name:
            _LOG.warning("DataQ dbt callback skipped: could not resolve project name")
            return

        status = status_from_results(results)
        body = build_payload(
            project_name=str(project_name),
            job_name=str(job_name),
            invocation_id=str(metadata["invocation_id"]),
            status=status,
            started_at=metadata.get("invocation_started_at"),
            finished_at=metadata.get("generated_at"),
        )
        http_status = _post(url, body, sign(secret, body))
        _LOG.info(
            "DataQ dbt callback delivered: project=%s job=%s status=%s http=%s",
            project_name,
            job_name,
            status,
            http_status,
        )
    except urllib.error.HTTPError as exc:
        # DataQ replied non-2xx (e.g. 401 bad signature, 422 malformed). Log, never
        # raise — the build is already complete and unaffected.
        _LOG.warning("DataQ dbt callback rejected: http=%s reason=%s", exc.code, exc.reason)
    except Exception:
        _LOG.exception("DataQ dbt callback failed (build is unaffected)")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    notify(sys.argv[1] if len(sys.argv) > 1 else _DEFAULT_RESULTS_PATH)
