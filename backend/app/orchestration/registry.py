"""Orchestration-provider registry — provider value → `OrchestrationProvider`."""

from __future__ import annotations

from backend.app.orchestration.adf import AdfProvider
from backend.app.orchestration.airflow import AirflowProvider
from backend.app.orchestration.base import OrchestrationProvider
from backend.app.orchestration.dbt import DbtProvider


class UnsupportedProviderError(ValueError):
    """Raised when no provider is registered for an orchestration provider value."""


_PROVIDERS: dict[str, OrchestrationProvider] = {
    "adf": AdfProvider(),
    "airflow": AirflowProvider(),
    "dbt": DbtProvider(),
}


def get_orchestration_provider(provider: str) -> OrchestrationProvider:
    impl = _PROVIDERS.get(provider)
    if impl is None:
        raise UnsupportedProviderError(f"No orchestration provider registered for {provider!r}")
    return impl
