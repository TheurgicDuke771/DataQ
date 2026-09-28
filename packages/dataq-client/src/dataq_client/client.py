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
from dataq_client.generated.api.runs import get_run
from dataq_client.generated.api.suites import export_suite, import_suite, trigger_suite_run
from dataq_client.generated.client import AuthenticatedClient
from dataq_client.generated.models.incident_action_request import IncidentActionRequest
from dataq_client.generated.models.suite_import_request import SuiteImportRequest
from dataq_client.generated.types import Response

#: The server's run lifecycle; a run in one of the last three will not change again.
RUN_STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")
TERMINAL_RUN_STATUSES = frozenset({"succeeded", "failed", "cancelled"})

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


def _error_from(response: Response[Any]) -> DataQError:
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

        Polls at ``interval`` seconds (at least 5), backing off to 30 s. A 429 is raised, not
        retried. Raises `RunTimeoutError` if the run is still going after ``timeout`` seconds.
        """
        wait = max(float(interval), MIN_POLL_INTERVAL)
        deadline = clock() + timeout
        while True:
            outcome = self.get_run(run_id)
            if outcome.finished:
                return outcome
            remaining = deadline - clock()
            if remaining <= 0:
                raise RunTimeoutError(outcome.run_id, outcome.status, timeout)
            sleep(min(wait, remaining))
            wait = min(wait * 1.5, MAX_POLL_INTERVAL)

    def export_suite(self, suite_id: uuid.UUID | str) -> dict[str, Any]:
        """The suite's portable document (its checks and thresholds, no connection or grants)."""
        response = export_suite.sync_detailed(uuid.UUID(str(suite_id)), client=self.api)
        _ok(response)
        document: dict[str, Any] = json.loads(response.content)
        return document

    def import_suite(self, document: dict[str, Any], connection_id: uuid.UUID | str) -> Any:
        """Create a new suite from an exported document, bound to ``connection_id``."""
        body = SuiteImportRequest.from_dict(
            {"connection_id": str(connection_id), "document": document}
        )
        return _ok(import_suite.sync_detailed(client=self.api, body=body))

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
