"""OrchestrationProvider seam (ADF now; Airflow next) — ADR 0004."""

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
    """One pipeline/DAG run observation, normalised across providers."""

    provider_run_id: str
    pipeline_or_dag_id: str
    resource_name: str
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    failure_reason: str | None = None


@dataclass(frozen=True)
class AlertPing:
    """A webhook event that signals *something happened* without a run identity."""

    monitor_condition: str  # "fired" | "resolved" (lower-cased)
    resource_name: str | None = None  # e.g. the ADF factory name, when derivable
    pipeline_or_dag_id: str | None = None  # alert dimension, when present
    fired_at: datetime | None = None


@runtime_checkable
class OrchestrationProvider(Protocol):
    """Provider-agnostic monitoring interface — ADF reference impl, Airflow next."""

    provider: str
    # The `connections.config` JSONB key whose value a `RunUpdate.resource_name` is matched against
    # to attribute a run to an orchestrator connection (`factory_name` for ADF.
    resource_config_key: str

    def parse_event(self, payload: bytes, headers: Mapping[str, str]) -> RunUpdate | AlertPing:
        """Authenticated webhook body → normalised `RunUpdate`, or an
        `AlertPing` when the event has no run identity (alert-schema channel).
        """
        ...

    def fetch_run_detail(
        self, config: Mapping[str, Any], secret: str, provider_run_id: str
    ) -> RunUpdate:
        """Authoritative REST lookup of a single run, used to enrich a webhook event before
        persistence. ``config`` is the orchestrator connection's config (factory / subscription
        / SP identity); ``secret`` is its credential. Raises on transport / auth failure — the
        caller decides whether to fail soft.
        """
        ...

    def list_recent_runs(
        self, config: Mapping[str, Any], secret: str, since: datetime
    ) -> list[RunUpdate]:
        """REST poll for the 10-min fallback path: the provider's recent
        **succeeded** runs updated at/after ``since``, normalised to `RunUpdate`.
        """
        ...
