"""Shared fake `SecretStore` test double (#1251, generalized #1270)."""

from __future__ import annotations

from typing import Any

from backend.app.core.secrets import SecretNotFoundError


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
