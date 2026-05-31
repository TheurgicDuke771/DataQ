"""OrchestrationProvider seam (ADF now; Airflow next) — ADR 0004.

Both orchestration providers (ADF, Airflow) expose their pipeline/DAG run
activity to DataQ behind one interface that speaks provider-agnostic DTOs, so
service code routes by the `provider` value and never branches on the concrete
provider. ADF is the reference implementation (`orchestration/adf.py`).

The three responsibilities (ADR 0004) map onto the three methods:

- `parse_event`  — webhook payload → `RunUpdate` (near-real-time channel).
- `fetch_run_detail` — REST follow-up to enrich one run (deferred to the polling PR).
- `list_recent_runs` — REST poll for the fallback path (Week 5).

`RunUpdate` is the normalised shape both channels produce; the persistence layer
(`services/orchestration_service.py`) consumes only this, never a provider's raw
payload.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from backend.app.core.errors import DataQError


class MalformedEventError(DataQError):
    """A well-authenticated event whose body is missing required fields → 422."""

    status_code = 422
    code = "orchestration_event_malformed"


@dataclass(frozen=True)
class RunUpdate:
    """One pipeline/DAG run observation, normalised across providers.

    Maps onto a `pipeline_runs` row. `provider_run_id` is the provider's own run
    identifier and the idempotency key (with `provider`) for the upsert —
    replayed deliveries land on the same row. `resource_name` is the ADF
    factory name / Airflow host used to resolve which orchestrator connection
    (and thus which `env`) the run belongs to. `status` is already mapped to the
    DataQ `PIPELINE_RUN_STATUSES` value set.
    """

    provider_run_id: str
    pipeline_or_dag_id: str
    resource_name: str
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    failure_reason: str | None = None


@runtime_checkable
class OrchestrationProvider(Protocol):
    """Provider-agnostic monitoring interface — ADF reference impl, Airflow next."""

    provider: str

    def parse_event(self, payload: bytes, headers: Mapping[str, str]) -> RunUpdate:
        """Authenticated webhook body → normalised `RunUpdate`.

        Raises `MalformedEventError` when required fields are absent.
        """
        ...

    def fetch_run_detail(self, resource_name: str, provider_run_id: str) -> RunUpdate:
        """REST follow-up to enrich a single run. (Polling PR.)"""
        ...

    def list_recent_runs(self, since: datetime) -> list[RunUpdate]:
        """REST poll for the 10-min fallback path. (Week 5.)"""
        ...
