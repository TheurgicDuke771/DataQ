"""The hand-written layer over the generated client: the CI and notebook workflows.

Everything else in the REST API is reachable through ``DataQClient.api`` (the generated
``AuthenticatedClient``) and the modules under ``dataq_client.generated.api``.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from dataq_client.generated.api.incidents import acknowledge_incident, resolve_incident
from dataq_client.generated.api.runs import get_run, get_run_progress, pipeline_gate
from dataq_client.generated.api.suites import (
    apply_suite_document,
    export_suite,
    import_suite,
    trigger_suite_run,
    validate_suite_document,
)
from dataq_client.generated.client import AuthenticatedClient
from dataq_client.generated.models.gate_request import GateRequest
from dataq_client.generated.models.gate_request_env import GateRequestEnv
from dataq_client.generated.models.gate_request_fail_on import GateRequestFailOn
from dataq_client.generated.models.gate_request_provider import GateRequestProvider
from dataq_client.generated.models.incident_action_request import IncidentActionRequest
from dataq_client.generated.models.suite_apply_request import SuiteApplyRequest
from dataq_client.generated.models.suite_import_request import SuiteImportRequest
from dataq_client.generated.models.suite_validate_request import SuiteValidateRequest
from dataq_client.generated.types import Response

#: The server's run lifecycle; a run in one of the last three will not change again.
RUN_STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")
TERMINAL_RUN_STATUSES = frozenset({"succeeded", "failed", "cancelled"})

#: A pipeline gate's answers; the last three are final.
GATE_STATES = ("awaiting_trigger", "running", "passed", "failed", "error")
TERMINAL_GATE_STATES = frozenset({"passed", "failed", "error"})

#: Polls never come faster than this: a PAT is rate-limited like any other caller.
MIN_POLL_INTERVAL = 5.0
MAX_POLL_INTERVAL = 30.0


class DataQError(Exception):
    """The API refused a request. ``code`` is the server's ``error.code``: branch on it, never on
    the message."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(f"{status_code} {code}: {message}")
        self.status_code = status_code
        self.code = code
        self.message = message


class AuthError(DataQError):
    """401 or 403: the token is missing, revoked, or not allowed to do this."""


class RateLimitedError(DataQError):
    """429. Raised, never retried in a loop: back off before calling again."""


class GateTimeoutError(Exception):
    """The gate had no final answer within the timeout."""

    def __init__(self, state: str, timeout: float) -> None:
        super().__init__(f"the gate was still {state} after {timeout:g}s")
        self.state = state
        self.timeout = timeout


@dataclass(frozen=True)
class GateOutcome:
    """A pipeline gate's answer (ADR 0046): ``state`` plus each bound suite's run."""

    state: str
    fail_on: str
    triggered_by: str
    suites: list[dict[str, Any]]

    @property
    def finished(self) -> bool:
        return self.state in TERMINAL_GATE_STATES


class RunTimeoutError(Exception):
    """The run was still going when ``wait_for_run`` gave up. It keeps running on the server."""

    def __init__(self, run_id: uuid.UUID, status: str, timeout: float) -> None:
        super().__init__(f"run {run_id} still {status} after {timeout:g}s")
        self.run_id = run_id
        self.status = status


@dataclass(frozen=True)
class RunOutcome:
    """A run's state, with **lifecycle and data quality as separate fields**.

    ``status`` is whether the run itself finished (``succeeded`` / ``failed`` / ``cancelled``, or
    ``queued`` / ``running`` if it has not). ``worst_severity`` is the worst check result
    (``warn`` / ``fail`` / ``critical``, or ``None`` when nothing failed). A run can succeed with
    a critical failure, and fail with no results at all — never collapse the two.
    """

    run_id: uuid.UUID
    suite_id: uuid.UUID
    status: str
    worst_severity: str | None
    checks_total: int
    checks_passed: int
    #: Checks that could not be evaluated at all (the ``error`` result status).
    checks_errored: int
    failure_reason: str | None

    @property
    def finished(self) -> bool:
        return self.status in TERMINAL_RUN_STATUSES


def _error_from(response: Any) -> DataQError:
    """``response`` is a generated ``Response`` or a raw ``httpx.Response``: both carry
    ``status_code`` and ``content``."""
    status = int(response.status_code)
    code, message = f"http_{status}", response.content.decode("utf-8", "replace")[:500]
    try:
        body = json.loads(response.content)
    except ValueError:
        body = None
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        code = str(body["error"].get("code") or code)
        message = str(body["error"].get("message") or message)
    elif isinstance(body, dict) and "detail" in body:
        message = str(body["detail"])[:500]
    kind: type[DataQError] = DataQError
    if status in (401, 403):
        kind = AuthError
    elif status == 429:
        kind = RateLimitedError
    return kind(status, code, message)


def _ok(response: Response[Any]) -> Any:
    if not 200 <= int(response.status_code) < 300:
        raise _error_from(response)
    if response.parsed is None:
        # A success status the spec does not document: the server and client versions disagree.
        raise DataQError(
            int(response.status_code),
            "unexpected_response",
            "the server answered with a status this client version does not expect",
        )
    return response.parsed


def _document_field(document: dict[str, Any] | str) -> dict[str, Any]:
    """YAML travels as text and is parsed by the server, so this client needs no YAML
    library and cannot disagree with the server about what a value means."""
    return {"document_yaml": document} if isinstance(document, str) else {"document": document}


def _json_body(response: Response[Any]) -> dict[str, Any]:
    if not 200 <= int(response.status_code) < 300:
        raise _error_from(response)
    body: dict[str, Any] = json.loads(response.content)
    return body


def _outcome(run: Any) -> RunOutcome:
    results = getattr(run, "results", None) or []
    return RunOutcome(
        run_id=run.id,
        suite_id=run.suite_id,
        status=run.status,
        worst_severity=run.worst_severity if isinstance(run.worst_severity, str) else None,
        checks_total=run.checks_total if isinstance(run.checks_total, int) else 0,
        checks_passed=run.checks_passed if isinstance(run.checks_passed, int) else 0,
        checks_errored=sum(1 for result in results if result.status == "error"),
        failure_reason=run.failure_reason if isinstance(run.failure_reason, str) else None,
    )


class DataQClient:
    """A DataQ API client authenticated with a personal access token.

    ``base_url`` is the deployment's public host (the one that serves the web app); ``pat`` a
    ``dq_live_…`` token. Both fall back to the ``DATAQ_URL`` / ``DATAQ_PAT`` environment
    variables. Nothing is read from files or a keyring.
    """

    def __init__(
        self,
        base_url: str | None = None,
        pat: str | None = None,
        *,
        timeout: float = 30.0,
        httpx_args: dict[str, Any] | None = None,
    ) -> None:
        base_url = base_url or os.environ.get("DATAQ_URL")
        pat = pat or os.environ.get("DATAQ_PAT")
        if not base_url:
            raise ValueError("base_url is required (or set DATAQ_URL)")
        if not pat:
            raise ValueError("pat is required (or set DATAQ_PAT)")
        import httpx

        self.api = AuthenticatedClient(
            base_url=base_url.rstrip("/"),
            token=pat,
            timeout=httpx.Timeout(timeout),
            httpx_args=httpx_args or {},
        )

    def __enter__(self) -> DataQClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.api.get_httpx_client().close()

    def trigger_run(self, suite_id: uuid.UUID | str) -> RunOutcome:
        """Queue a run of the suite. The outcome is the run as queued — not finished."""
        run = _ok(trigger_suite_run.sync_detailed(uuid.UUID(str(suite_id)), client=self.api))
        return _outcome(run)

    def get_run(self, run_id: uuid.UUID | str) -> RunOutcome:
        return _outcome(_ok(get_run.sync_detailed(uuid.UUID(str(run_id)), client=self.api)))

    def wait_for_run(
        self,
        run_id: uuid.UUID | str,
        *,
        timeout: float = 1800.0,
        interval: float = MIN_POLL_INTERVAL,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> RunOutcome:
        """Poll the run until it finishes, reading its lifecycle ``status`` — never the check
        counts, which describe a partial run while it is still going.

        Polls the lightweight progress endpoint and reads the full run once, at the end: every
        read of the full run returns its sample rows and is recorded in the audit log.
        Polls at ``interval`` seconds (at least 5), backing off to 30 s. A 429 is raised, not
        retried. Raises `RunTimeoutError` if the run is still going after ``timeout`` seconds.
        """
        run_uuid = uuid.UUID(str(run_id))
        wait = max(float(interval), MIN_POLL_INTERVAL)
        deadline = clock() + timeout
        while True:
            progress = _ok(get_run_progress.sync_detailed(run_uuid, client=self.api))
            if progress.status in TERMINAL_RUN_STATUSES:
                return self.get_run(run_uuid)
            remaining = deadline - clock()
            if remaining <= 0:
                raise RunTimeoutError(run_uuid, progress.status, timeout)
            sleep(min(wait, remaining))
            wait = min(wait * 1.5, MAX_POLL_INTERVAL)

    def gate(
        self,
        *,
        provider: str,
        pipeline_or_dag_id: str,
        env: str,
        provider_run_id: str,
        fail_on: str = "fail",
        trigger: bool = True,
        timeout: float = 1800.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> GateOutcome:
        """Ask whether a pipeline may continue, and wait for the answer.

        With ``trigger`` (needs ``edit`` on the bound suites) the first request starts their
        runs for this ``provider_run_id``; every later poll is the same idempotent request.
        Without it, the gate only reports on runs already triggered for it (``view``).
        Returns once ``state`` is ``passed``, ``failed`` or ``error``; raises
        `GateTimeoutError` after ``timeout`` seconds. Polls at the server's
        ``retry_after_seconds`` hint, never faster than 5 s.
        """
        body = GateRequest(
            provider=GateRequestProvider(provider),
            pipeline_or_dag_id=pipeline_or_dag_id,
            env=GateRequestEnv(env),
            provider_run_id=provider_run_id,
            fail_on=GateRequestFailOn(fail_on),
            trigger=trigger,
        )
        deadline = clock() + timeout
        while True:
            read = _ok(pipeline_gate.sync_detailed(client=self.api, body=body))
            state = read.state.value
            if state in TERMINAL_GATE_STATES:
                return GateOutcome(
                    state=state,
                    fail_on=read.fail_on,
                    triggered_by=read.triggered_by,
                    suites=[suite.to_dict() for suite in read.suites],
                )
            remaining = deadline - clock()
            if remaining <= 0:
                raise GateTimeoutError(state, timeout)
            hint = float(read.retry_after_seconds or MIN_POLL_INTERVAL)
            sleep(min(max(hint, MIN_POLL_INTERVAL), remaining))

    def export_suite(self, suite_id: uuid.UUID | str) -> dict[str, Any]:
        """The suite's portable document (its checks and thresholds, no connection or grants)."""
        response = export_suite.sync_detailed(uuid.UUID(str(suite_id)), client=self.api)
        _ok(response)
        document: dict[str, Any] = json.loads(response.content)
        return document

    def export_suite_yaml(self, suite_id: uuid.UUID | str) -> str:
        """The suite's document as YAML text."""
        # Requested directly: the generated call parses every 200 body as JSON.
        response = self.api.get_httpx_client().get(
            f"/api/v1/suites/{uuid.UUID(str(suite_id))}/export", params={"format": "yaml"}
        )
        if not 200 <= response.status_code < 300:
            raise _error_from(response)
        return response.text

    def import_suite(self, document: dict[str, Any] | str, connection_id: uuid.UUID | str) -> Any:
        """Create a new suite from a document, bound to ``connection_id``. ``document`` is
        the JSON document as a dict, or YAML text."""
        body = SuiteImportRequest.from_dict(
            {"connection_id": str(connection_id), **_document_field(document)}
        )
        return _ok(import_suite.sync_detailed(client=self.api, body=body))

    def validate_suite(
        self, document: dict[str, Any] | str, connection_id: uuid.UUID | str
    ) -> dict[str, Any]:
        """Whether importing ``document`` onto ``connection_id`` would be accepted, with
        every problem if not: ``{"valid", "check_count", "problems"}``. Creates nothing,
        and opens no datasource — a valid document can still name a column that does not
        exist."""
        body = SuiteValidateRequest.from_dict(
            {"connection_id": str(connection_id), **_document_field(document)}
        )
        return _json_body(validate_suite_document.sync_detailed(client=self.api, body=body))

    def apply_suite(
        self,
        suite_id: uuid.UUID | str,
        document: dict[str, Any] | str,
        *,
        prune: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Bring an existing suite in line with ``document``; checks are matched by name.
        With ``dry_run`` nothing is written and ``changed`` reports drift. ``prune`` also
        deletes checks the document does not name, with their results and history."""
        body = SuiteApplyRequest.from_dict(
            {**_document_field(document), "prune": prune, "dry_run": dry_run}
        )
        return _json_body(
            apply_suite_document.sync_detailed(uuid.UUID(str(suite_id)), client=self.api, body=body)
        )

    def acknowledge_incident(self, incident_id: uuid.UUID | str, note: str | None = None) -> Any:
        body = IncidentActionRequest.from_dict({} if note is None else {"note": note})
        return _ok(
            acknowledge_incident.sync_detailed(
                uuid.UUID(str(incident_id)), client=self.api, body=body
            )
        )

    def resolve_incident(self, incident_id: uuid.UUID | str, note: str | None = None) -> Any:
        body = IncidentActionRequest.from_dict({} if note is None else {"note": note})
        return _ok(
            resolve_incident.sync_detailed(uuid.UUID(str(incident_id)), client=self.api, body=body)
        )
