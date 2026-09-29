"""DataQ ↔ Apache Airflow pipeline gate (copy into your DAGs folder beside the callback).

Put a sensor between the stage that lands data and the stages that consume it::

    from airflow.sensors.python import PythonSensor
    from dataq_gate import dataq_gate

    gate = PythonSensor(
        task_id="dataq_gate",
        python_callable=dataq_gate,
        mode="reschedule",   # frees the worker slot between pokes
        poke_interval=30,
        timeout=1800,
    )
    load >> gate >> publish

The first poke asks DataQ to run every suite bound to this DAG for this DAG run, and each later
poke repeats the same request (DataQ starts the runs once). The sensor succeeds when the gate
**passed**, keeps waiting while it is running, and **fails the task** when it failed or errored,
so the stages after it never run on bad data (DataQ ADR 0046). Unlike the callback snippet this
one is fail-closed: a gate that cannot reach DataQ raises, and Airflow's own retries and the
sensor timeout decide what happens next.

Configuration — environment variables, read at call time:

    DATAQ_URL        The DataQ host, e.g. https://dataq.example.com
    DATAQ_PAT        A personal access token (``dq_live_…``) of a user with **edit** on every
                     suite bound to the DAG (``view`` is enough with ``DATAQ_GATE_TRIGGER=false``).
    DATAQ_ENV        The binding's env: dev, qa, uat or prod.
    DATAQ_GATE_FAIL_ON   Optional: warn, fail (default) or critical.
    DATAQ_GATE_TRIGGER   Optional: "false" to only report on runs the DAG's success event
                         already triggered (for a downstream DAG gating on an upstream one).

``dataq_gate`` reads ``dag_id`` and ``run_id`` from the task context; pass
``op_kwargs={"pipeline_or_dag_id": "...", "provider_run_id": "..."}`` to gate on another DAG's
run. Stdlib only.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Any

_LOG = logging.getLogger(__name__)
_FINAL_FAILURES = ("failed", "error")


class DataQGateFailedError(Exception):
    """The gate said stop. Raised as AirflowFailException when Airflow is importable."""


def _fail(message: str) -> Exception:
    try:
        from airflow.exceptions import AirflowFailException  # retries would not change the verdict

        return AirflowFailException(message)
    except ImportError:  # pragma: no cover - outside Airflow
        return DataQGateFailedError(message)


def dataq_gate(
    pipeline_or_dag_id: str | None = None,
    provider_run_id: str | None = None,
    **context: Any,
) -> bool:
    """One poke: ``True`` when the gate passed, ``False`` while it runs; raises on a stop."""
    body = {
        "provider": "airflow",
        "pipeline_or_dag_id": pipeline_or_dag_id or context["dag"].dag_id,
        "env": os.environ["DATAQ_ENV"],
        "provider_run_id": provider_run_id or context["run_id"],
        "fail_on": os.environ.get("DATAQ_GATE_FAIL_ON", "fail"),
        "trigger": os.environ.get("DATAQ_GATE_TRIGGER", "true").lower() != "false",
    }
    request = urllib.request.Request(  # noqa: S310 — url is operator-configured, not user input
        os.environ["DATAQ_URL"].rstrip("/") + "/api/v1/orchestration/gate",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {os.environ['DATAQ_PAT']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        verdict = json.load(response)
    state = verdict["state"]
    _LOG.info("dataq gate %s: %s", verdict["triggered_by"], state)
    if state in _FINAL_FAILURES:
        failing = [s["suite_id"] for s in verdict["suites"] if s["state"] in _FINAL_FAILURES]
        raise _fail(f"DataQ gate {state} for {verdict['triggered_by']} (suites {failing})")
    return bool(state == "passed")
