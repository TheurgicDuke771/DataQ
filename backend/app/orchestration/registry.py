"""Orchestration-provider registry — provider value → `OrchestrationProvider`.

The single place mapping a `pipeline_runs.provider` value (`adf` now; `airflow`
next) to its implementation. Adding Airflow is a one-line entry plus the
provider class; the webhook endpoint and persistence service dispatch through
`get_orchestration_provider` and never branch on the provider.
"""

from __future__ import annotations

from backend.app.orchestration.adf import AdfProvider
from backend.app.orchestration.airflow import AirflowProvider
from backend.app.orchestration.base import OrchestrationProvider


class UnsupportedProviderError(ValueError):
    """Raised when no provider is registered for an orchestration provider value."""


_PROVIDERS: dict[str, OrchestrationProvider] = {
    "adf": AdfProvider(),
    "airflow": AirflowProvider(),
}


def get_orchestration_provider(provider: str) -> OrchestrationProvider:
    impl = _PROVIDERS.get(provider)
    if impl is None:
        raise UnsupportedProviderError(f"No orchestration provider registered for {provider!r}")
    return impl
