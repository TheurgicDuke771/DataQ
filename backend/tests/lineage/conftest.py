"""Shared fixtures for the lineage tests.

Env hygiene: every test starts with the OpenLineage transport vars unset and the
cached client cleared, so tests can't leak configuration into each other (a stray
``OPENLINEAGE_URL`` would otherwise flip the whole suite out of the dark path).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from backend.app.lineage import emitter

_OL_ENV_VARS = (
    "OPENLINEAGE_URL",
    "OPENLINEAGE__TRANSPORT__TYPE",
    "OPENLINEAGE_CONFIG",
    "OPENLINEAGE_DISABLED",
)


@pytest.fixture(autouse=True)
def _clean_openlineage_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for var in _OL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    emitter.reset_openlineage_client_cache()
    yield
    emitter.reset_openlineage_client_cache()
