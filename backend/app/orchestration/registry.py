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


def provider_display_names() -> dict[str, str]:
    """Provider value → the name shown to people, for every registered provider."""
    return {value: impl.display_name for value, impl in _PROVIDERS.items()}


def provider_display_name(provider: str) -> str:
    """The provider's display name; a value that is not a registered provider is returned
    as given, so an unknown or legacy value is shown rather than hidden.
    """
    impl = _PROVIDERS.get(provider)
    return impl.display_name if impl is not None else provider


def get_orchestration_provider(provider: str) -> OrchestrationProvider:
    impl = _PROVIDERS.get(provider)
    if impl is None:
        raise UnsupportedProviderError(f"No orchestration provider registered for {provider!r}")
    return impl
