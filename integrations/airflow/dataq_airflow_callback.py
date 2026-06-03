"""DataQ ↔ Apache Airflow DAG-run callback snippet (copy into your DAGs folder).

Wire the two callbacks onto each DAG you want DataQ to observe::

    from dataq_airflow_callback import on_dataq_success, on_dataq_failure

    with DAG(
        dag_id="load_finance",
        on_success_callback=on_dataq_success,
        on_failure_callback=on_dataq_failure,
        ...
    ):
        ...

On every DAG-run completion this POSTs a small, HMAC-signed JSON document to
DataQ's Airflow event receiver. DataQ records the run in ``pipeline_runs`` and,
on success, triggers any suite bound to this DAG (DataQ ADR 0004 / 0007). Both
success and failure are reported; only success fires a trigger.

Configuration — environment variables, read at call time (e.g. set as Airflow
``env`` or exported in the worker environment):

    DATAQ_WEBHOOK_URL       Full receiver URL, e.g.
                            https://dataq.example.com/api/v1/orchestration/events/airflow
    DATAQ_WEBHOOK_SECRET    HMAC signing key — the SAME value DataQ stores in Key
                            Vault as ``airflow-webhook-secret``.
    DATAQ_AIRFLOW_BASE_URL  This Airflow's webserver root, e.g.
                            https://airflow.example.com. MUST match the ``base_url``
                            of the Airflow connection registered in DataQ (that is
                            how DataQ attributes the run). Falls back to Airflow's
                            ``[webserver] base_url`` when unset.

Design notes: stdlib-only (no extra pip installs), and **fail-safe** — every
error is swallowed and logged, so a notification failure can never break your
DAG. The HMAC is computed over the exact bytes that are POSTed, matching DataQ's
constant-time check on the raw request body.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

_LOG = logging.getLogger(__name__)

_SIGNATURE_HEADER = "X-DataQ-Signature"
_TIMEOUT_SECONDS = 10


def build_payload(
    *,
    dag_id: str,
    run_id: str,
    state: str,
    base_url: str,
    start_date: str | None = None,
    end_date: str | None = None,
    error: str | None = None,
) -> bytes:
    """Serialise the event to the exact bytes that are signed and POSTed.

    Compact JSON, so the bytes are stable: the signature is computed over this
    return value and the same value is sent as the request body — it is never
    re-encoded in between (which would invalidate the signature).
    """
    doc: dict[str, Any] = {
        "dag_id": dag_id,
        "run_id": run_id,
        "state": state,
        "base_url": base_url,
    }
    if start_date:
        doc["start_date"] = start_date
    if end_date:
        doc["end_date"] = end_date
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


def _iso(value: Any) -> str | None:
    """Best-effort ISO-8601 string for an Airflow datetime (pendulum or stdlib)."""
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat())
    return str(value)


def _airflow_base_url() -> str | None:
    """Fall back to Airflow's configured webserver base_url, if importable."""
    try:
        from airflow.configuration import conf

        return str(conf.get("webserver", "base_url"))
    except Exception:
        return None


def _notify(context: dict[str, Any], state: str) -> None:
    """Build, sign, and POST one DAG-run event. Never raises."""
    url = os.environ.get("DATAQ_WEBHOOK_URL")
    secret = os.environ.get("DATAQ_WEBHOOK_SECRET")
    if not url or not secret:
        _LOG.warning("DataQ callback skipped: DATAQ_WEBHOOK_URL / DATAQ_WEBHOOK_SECRET not set")
        return

    try:
        dag_run = context.get("dag_run")
        dag_id = getattr(dag_run, "dag_id", None) or getattr(context.get("dag"), "dag_id", None)
        run_id = getattr(dag_run, "run_id", None)
        base_url = os.environ.get("DATAQ_AIRFLOW_BASE_URL") or _airflow_base_url()
        if not dag_id or not run_id or not base_url:
            _LOG.warning(
                "DataQ callback skipped: could not resolve dag_id/run_id/base_url from context"
            )
            return

        error = context.get("reason") if state == "failed" else None
        body = build_payload(
            dag_id=str(dag_id),
            run_id=str(run_id),
            state=state,
            base_url=str(base_url).rstrip("/"),
            start_date=_iso(getattr(dag_run, "start_date", None)),
            end_date=_iso(getattr(dag_run, "end_date", None)),
            error=str(error) if error else None,
        )
        http_status = _post(url, body, sign(secret, body))
        _LOG.info(
            "DataQ callback delivered: dag=%s run=%s state=%s http=%s",
            dag_id,
            run_id,
            state,
            http_status,
        )
    except urllib.error.HTTPError as exc:
        # DataQ replied non-2xx (e.g. 401 bad signature, 422 malformed). Log the
        # status, never raise — the DAG run is already complete and unaffected.
        _LOG.warning("DataQ callback rejected: http=%s reason=%s", exc.code, exc.reason)
    except Exception:
        _LOG.exception("DataQ callback failed (DAG run is unaffected)")


def on_dataq_success(context: dict[str, Any]) -> None:
    """DAG-level ``on_success_callback`` — report a succeeded DAG run to DataQ."""
    _notify(context, "success")


def on_dataq_failure(context: dict[str, Any]) -> None:
    """DAG-level ``on_failure_callback`` — report a failed DAG run to DataQ."""
    _notify(context, "failed")
