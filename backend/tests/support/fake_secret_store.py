"""Shared fake `SecretStore` test double (#1251, generalized #1270)."""

from __future__ import annotations

from typing import Any

from backend.app.core.secrets import SecretInfo, SecretNotFoundError


class FakeSecretStore:
    """In-memory `SecretStore` double. See module docstring for the modes."""

    def __init__(
        self,
        initial: dict[str, str] | None = None,
        *,
        default: str | None = None,
        raise_on_write: bool = False,
        raise_on_get: Exception | None = None,
        raise_on_set: Exception | None = None,
    ) -> None:
        self.data: dict[str, str] = dict(initial) if initial else {}
        self._default = default
        self._raise_on_get = raise_on_get
        self._raise_on_set = raise_on_set
        if raise_on_write and self._raise_on_set is None:
            self._raise_on_set = NotImplementedError()
        self.requested: list[str] = []
        self.writes: list[str] = []
        self.deleted: list[str] = []

    def get(self, name: str) -> str:
        self.requested.append(name)
        if self._raise_on_get is not None:
            raise self._raise_on_get
        if name in self.data:
            return self.data[name]
        if self._default is not None:
            return self._default
        raise SecretNotFoundError(name)

    def set(self, name: str, value: str) -> None:
        if self._raise_on_set is not None:
            raise self._raise_on_set
        self.writes.append(value)
        self.data[name] = value

    def delete(self, name: str) -> None:
        if self._raise_on_set is not None:
            raise self._raise_on_set
        self.deleted.append(name)
        self.data.pop(name, None)


def override_secret_store(app: Any, store: FakeSecretStore) -> None:
    """Install `store` as `app`'s `get_secret_store` override — always through a lambda, never
    `app.dependency_overrides[get_secret_store] = store` directly.
    """
    from backend.app.core.secrets import get_secret_store

    app.dependency_overrides[get_secret_store] = lambda: store


# ── Orphan-secret sweep (#1059) doubles ─────────────────────────────────────── Shared between
# `test_secret_sweep_service.py` and `test_secret_sweep_task.py` (#1886 review) so the two
# suites can't quietly diverge on what "a store that can/can't enumerate itself" means.


class EnumerableSecretStore(FakeSecretStore):
    """A store that can enumerate itself via `list_secrets()` — only OpenBao/AKV implement
    this for real; the sweep duck-types via `getattr` since `EnvSecretStore` and every other
    test double lack it. `get`/`set` deliberately raise: the sweep must never read or write a
    secret VALUE, only enumerate and (optionally) delete by name.
    """

    def __init__(self, secrets: list[SecretInfo]) -> None:
        super().__init__()
        self._secrets = secrets

    def get(self, name: str) -> str:  # pragma: no cover - not exercised
        raise AssertionError("the sweep must never read a secret VALUE")

    def set(self, name: str, value: str) -> None:  # pragma: no cover
        raise AssertionError("the sweep must never write")

    def list_secrets(self) -> list[SecretInfo]:
        return list(self._secrets)


class UnlistableSecretStore(EnumerableSecretStore):
    """Mirrors `EnvSecretStore` and every test double: no `list_secrets` at all — the sweep
    must treat this as "cannot enumerate", never as "an empty vault".
    """

    list_secrets = None  # type: ignore[assignment]


class BrokenSecretStore(EnumerableSecretStore):
    """A store whose `list_secrets()` always raises — an outage, never a clean/empty vault."""

    def __init__(
        self, secrets: list[SecretInfo] | None = None, *, error: Exception | None = None
    ) -> None:
        super().__init__(secrets or [])
        self._error = error or RuntimeError("vault sealed")

    def list_secrets(self) -> list[SecretInfo]:
        raise self._error
