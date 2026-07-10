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
from typing import Any, Protocol, runtime_checkable

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


@dataclass(frozen=True)
class AlertPing:
    """A webhook event that signals *something happened* without a run identity.

    Azure Monitor's Common Alert Schema (#492) is the canonical case: a metric
    alert on failed pipeline runs names the factory and (via dimensions) the
    pipeline, but carries **no runId** — so it cannot become a `RunUpdate`
    (whose `provider_run_id` is the upsert idempotency key). Instead the
    receiver treats it as a poll-now signal: the regular polling path ingests
    the real run(s) — true identity, status, timings — within seconds instead
    of the 10-min cadence. Provider-agnostic on purpose: any provider whose
    alerting channel is run-anonymous can return one.
    """

    monitor_condition: str  # "fired" | "resolved" (lower-cased)
    resource_name: str | None = None  # e.g. the ADF factory name, when derivable
    pipeline_or_dag_id: str | None = None  # alert dimension, when present
    fired_at: datetime | None = None


@runtime_checkable
class OrchestrationProvider(Protocol):
    """Provider-agnostic monitoring interface — ADF reference impl, Airflow next.

    **Optional capabilities (discovered via ``getattr``, not on this Protocol).**
    Some providers expose extra methods that only make sense for them; callers
    probe for them with ``getattr(provider_impl, "<name>", None)`` and no-op when
    absent, so the seam stays branch-free (CLAUDE.md §11) and adding one never
    forces every provider to implement it. Current set:

    - ``read_manifest(config: dict, secret: str, job: str) -> bytes | None`` — dbt
      only (ADR 0034, #759): reads a job's ``manifest.json`` for the lineage
      refresh. ``None`` when the manifest hasn't been published. Because the probe
      is ``getattr``-silent, a **typo** in the method name reads as "capability
      absent" — keep the name in sync with the caller
      (`orchestration_service._dispatch_lineage_refresh`).
    """

    provider: str
    # The `connections.config` JSONB key whose value a `RunUpdate.resource_name`
    # is matched against to attribute a run to an orchestrator connection
    # (`factory_name` for ADF, `base_url` for Airflow). Lets the persistence
    # layer resolve the connection without branching on the provider.
    resource_config_key: str

    def parse_event(self, payload: bytes, headers: Mapping[str, str]) -> RunUpdate | AlertPing:
        """Authenticated webhook body → normalised `RunUpdate`, or an
        `AlertPing` when the event has no run identity (alert-schema channel).

        Raises `MalformedEventError` when required fields are absent.
        """
        ...

    def fetch_run_detail(
        self, config: Mapping[str, Any], secret: str, provider_run_id: str
    ) -> RunUpdate:
        """Authoritative REST lookup of a single run, used to enrich a webhook
        event before persistence. ``config`` is the orchestrator connection's
        config (factory / subscription / SP identity); ``secret`` is its
        credential. Raises on transport / auth failure — the caller decides
        whether to fail soft."""
        ...

    def list_recent_runs(
        self, config: Mapping[str, Any], secret: str, since: datetime
    ) -> list[RunUpdate]:
        """REST poll for the 10-min fallback path: the provider's recent
        **succeeded** runs updated at/after ``since``, normalised to `RunUpdate`.

        ``config`` is the orchestrator connection's config and ``secret`` its
        credential (mirrors `fetch_run_detail`). Raises on transport / auth
        failure — the polling task fails soft per connection.
        """
        ...
