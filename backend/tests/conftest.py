"""Shared pytest fixtures."""

from collections.abc import Iterator

import pytest

from backend.app.core import secrets
from backend.app.core.config import get_settings


@pytest.fixture(autouse=True)
def _reset_caches() -> Iterator[None]:
    """Clear cached singletons between tests so settings + secret store rebuild."""
    get_settings.cache_clear()
    secrets.reset_secret_store_cache()
    yield
    get_settings.cache_clear()
    secrets.reset_secret_store_cache()


@pytest.fixture
def clean_kv_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every KV_SECRET_* env var so tests start from a clean slate."""
    import os

    for key in list(os.environ):
        if key.startswith("KV_SECRET_"):
            monkeypatch.delenv(key, raising=False)
