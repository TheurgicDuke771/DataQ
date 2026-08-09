"""Shared fake `SecretStore` test double (#1251).

Before this module existed, four-plus test files each hand-rolled their own
class implementing `get`/`set`/`delete` against the real `SecretStore`
Protocol (`backend.app.core.secrets.SecretStore`) — `test_admin.py::_FakeStore`,
`test_connections.py::FakeStore`/`_WriteFailStore`,
`test_dataset_reader.py::FakeSecretStore`, `test_comparison_run.py::FakeSecretStore`,
and `test_suites.py::_FakeBatchPreviewSecretStore`. They diverged only in ways
that are naturally constructor parameters — dict-backed vs. fixed-value,
read-only vs. writable — so this one class covers the union:

- **Dict-backed** (the default): `.set()`/`.delete()` mutate `self.data`, and
  `.get()` returns whatever was written. Pre-seed via `initial=`.
- **Fixed-value stub**: pass `default=` — any name not already in `self.data`
  resolves to `default` instead of raising, for tests that only care that
  *some* credential comes back, not which ref was asked for.
- **Missing-secret simulation**: with no `initial` and no `default`, `.get()`
  raises `SecretNotFoundError` for any name — matching the real store's
  contract for an unprovisioned secret.
- **Read-only guard**: `raise_on_write=True` makes `.set()`/`.delete()` raise
  `NotImplementedError`, for tests asserting a code path never writes a
  secret (the old `test_admin.py::_FakeStore` shape).

`.requested` records every name passed to `.get()`, for tests that assert
which ref the code under test actually resolved (the old
`test_dataset_reader.py::FakeSecretStore` shape).

A genuinely distinct failure mode — `.set()` raising `SecretWriteError` to
simulate an unreachable vault (`test_connections.py::_WriteFailStore`, #87) —
isn't a natural constructor flag here: it's a different *exception type* than
the "never called" guard above, so that one stays a small subclass next to
its call site instead of being folded in.

**Always install via `override_secret_store`, never assign the class (or a
subclass) directly to `app.dependency_overrides[get_secret_store]`.** FastAPI's
dependency-override resolution introspects the override CALLABLE's own
signature, not the original dependency's — a bare class whose `__init__` takes
parameters, even all-defaulted ones like this one's, gets those parameters
bound as request-level params, corrupting body validation for the endpoint
under test (found in #1251: a bare `_WriteFailStore()` override made
`POST /connections` return 422 instead of ever reaching the route).
"""

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
    ) -> None:
        self.data: dict[str, str] = dict(initial) if initial else {}
        self._default = default
        self._raise_on_write = raise_on_write
        self.requested: list[str] = []

    def get(self, name: str) -> str:
        self.requested.append(name)
        if name in self.data:
            return self.data[name]
        if self._default is not None:
            return self._default
        raise SecretNotFoundError(name)

    def set(self, name: str, value: str) -> None:
        if self._raise_on_write:
            raise NotImplementedError
        self.data[name] = value

    def delete(self, name: str) -> None:
        if self._raise_on_write:
            raise NotImplementedError
        self.data.pop(name, None)


def override_secret_store(app: Any, store: FakeSecretStore) -> None:
    """Install `store` as `app`'s `get_secret_store` override — always through a
    lambda, never `app.dependency_overrides[get_secret_store] = store` directly.
    See the module docstring: a bare class/instance assignment lets FastAPI's
    override-signature introspection reach into `FakeSecretStore.__init__`'s
    params and corrupt request body validation for the endpoint under test.
    `app` is typed `Any` to avoid importing FastAPI's `FastAPI` just for a
    one-line helper every call site already has the app instance for."""
    from backend.app.core.secrets import get_secret_store

    app.dependency_overrides[get_secret_store] = lambda: store
