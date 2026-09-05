"""Wiring test for the `sweep_orphan_secrets` task (#1059, persistence #1886)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest

from backend.app.core.config import get_settings
from backend.app.core.secrets import SecretInfo
from backend.app.services import secret_sweep_service
from backend.app.worker import tasks
from backend.tests.support.fake_secret_store import FakeSecretStore

_SECRET_VALUE = "sk-super-secret-do-not-persist-me"  # pragma: allowlist secret


class _EnumerableStore(FakeSecretStore):
    def __init__(self, secrets: list[SecretInfo]) -> None:
        super().__init__()
        self._secrets = secrets

    def get(self, name: str) -> str:
        return _SECRET_VALUE

    def list_secrets(self) -> list[SecretInfo]:
        return list(self._secrets)


class _BrokenStore(_EnumerableStore):
    def list_secrets(self) -> list[SecretInfo]:
        raise RuntimeError("connection refused")


@pytest.fixture(autouse=True)
def _default_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("SECRET_ORPHAN_GRACE_DAYS", "30")
    monkeypatch.setenv("SECRET_ORPHAN_PURGE", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _orphan(name: str) -> SecretInfo:
    return SecretInfo(name, datetime(2020, 1, 1, tzinfo=UTC))


def test_report_mode_persists_names_only_and_a_true_count(
    monkeypatch: pytest.MonkeyPatch, db_session: Any
) -> None:
    store = _EnumerableStore([_orphan("conn-dead-dev-abc123")])
    monkeypatch.setattr(tasks, "get_session", lambda: db_session)
    monkeypatch.setattr(tasks, "get_secret_store", lambda: store)

    count = tasks.sweep_orphan_secrets()

    assert count == 1
    report = secret_sweep_service.read_sweep_report(db_session)
    assert report is not None
    assert report.mode == "report"
    assert report.orphan_count == 1
    assert report.orphan_names == ["conn-dead-dev-abc123"]
    assert report.error is None
    # The secret VALUE must never reach the persisted row, even though the fake
    # store could return one.
    assert _SECRET_VALUE not in str(report)


def test_purge_setting_is_recorded_as_purge_mode(
    monkeypatch: pytest.MonkeyPatch, db_session: Any
) -> None:
    monkeypatch.setenv("SECRET_ORPHAN_PURGE", "true")
    get_settings.cache_clear()
    store = _EnumerableStore([_orphan("conn-dead-dev-abc123")])
    monkeypatch.setattr(tasks, "get_session", lambda: db_session)
    monkeypatch.setattr(tasks, "get_secret_store", lambda: store)

    tasks.sweep_orphan_secrets()

    report = secret_sweep_service.read_sweep_report(db_session)
    assert report is not None
    assert report.mode == "purge"
    assert store.deleted == ["conn-dead-dev-abc123"]


def test_force_report_only_overrides_the_purge_setting(
    monkeypatch: pytest.MonkeyPatch, db_session: Any
) -> None:
    """The UI-triggered "run now" path (#1886) must never purge, even with
    `SECRET_ORPHAN_PURGE=true` — this is what makes it safe to expose over the API.
    """
    monkeypatch.setenv("SECRET_ORPHAN_PURGE", "true")
    get_settings.cache_clear()
    store = _EnumerableStore([_orphan("conn-dead-dev-abc123")])
    monkeypatch.setattr(tasks, "get_session", lambda: db_session)
    monkeypatch.setattr(tasks, "get_secret_store", lambda: store)

    tasks.sweep_orphan_secrets(force_report_only=True)

    report = secret_sweep_service.read_sweep_report(db_session)
    assert report is not None
    assert report.mode == "report"
    assert store.deleted == []  # nothing was actually purged


def test_a_store_outage_persists_null_count_and_a_classified_error(
    monkeypatch: pytest.MonkeyPatch, db_session: Any
) -> None:
    """An unreachable store must read `orphan_count=None` + `error` — never `0`
    (the #954 masquerade, applied here per ADR 0039).
    """
    store = _BrokenStore([])
    monkeypatch.setattr(tasks, "get_session", lambda: db_session)
    monkeypatch.setattr(tasks, "get_secret_store", lambda: store)

    result = tasks.sweep_orphan_secrets()

    assert result == 0  # the task's own return value stays the existing int contract
    report = secret_sweep_service.read_sweep_report(db_session)
    assert report is not None
    assert report.orphan_count is None
    assert report.orphan_names == []
    assert report.error is not None
    assert "reach" in report.error.lower() or "connect" in report.error.lower()
