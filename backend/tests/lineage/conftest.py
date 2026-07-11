"""Shared fixtures for the lineage tests.

Env hygiene: every test starts with the OpenLineage transport vars unset and the
cached client cleared, so tests can't leak configuration into each other (a stray
``OPENLINEAGE_URL`` would otherwise flip the whole suite out of the dark path).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from backend.app.core.config import get_settings
from backend.app.lineage import emitter

_OL_ENV_VARS = (
    "OPENLINEAGE_URL",
    "OPENLINEAGE__TRANSPORT__TYPE",
    "OPENLINEAGE_CONFIG",
    "OPENLINEAGE_DISABLED",
    # Lineage catalog-pull gate (#762) — cleared too so a stray LINEAGE_PROVIDER /
    # MARQUEZ_URL can't flip the pull-provider factory out of the dark path mid-suite.
    "LINEAGE_PROVIDER",
    "MARQUEZ_URL",
)


@pytest.fixture(autouse=True)
def _clean_openlineage_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for var in _OL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    # The gate reads typed Settings (lru_cache'd) — drop any instance built before
    # the delenv so this test starts genuinely dark. The client cache mirrors it.
    get_settings.cache_clear()
    emitter.reset_openlineage_client_cache()
    yield
    emitter.reset_openlineage_client_cache()
