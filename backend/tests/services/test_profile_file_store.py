"""One secret read and one store client per flat-file suggest (#2235).

Counted where the cost is paid: at the secret store's `get`, and at the three functions
that construct a store client (`flatfile._s3_client` / `_blob_service` / `_gcs_client`).
Everything above them (`StoreSession`, `RangeReader`, `object_size`, `read_range`, the
CSV / Parquet / JSON readers) is the real code, so a reader that builds its own session
shows up here as a second client.
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from backend.app.datasources import flatfile
from backend.app.services import profile_service as svc
from backend.app.services.profile_service import ProfileFailedError
from backend.tests.support.fake_secret_store import FakeSecretStore

_LANDED = datetime(2026, 7, 1, tzinfo=UTC)
_CONFIGS: dict[str, dict[str, Any]] = {
    "s3": {"bucket": "raw", "region": "us-west-2", "access_key_id": "AKIAX"},
    "adls_gen2": {"account_url": "https://acct.blob.core.windows.net", "container": "raw"},
    "gcs": {"bucket": "raw", "auth_type": "service_account_key"},
}
_FACTORIES = {"s3": "_s3_client", "adls_gen2": "_blob_service", "gcs": "_gcs_client"}


def _frame() -> pd.DataFrame:
    return pd.DataFrame({"id": [1, 2, 3], "email": ["a@x.io", "b@x.io", None]})


def _content(fmt: str) -> bytes:
    if fmt == "csv":
        return bytes(_frame().to_csv(index=False).encode())
    if fmt == "json":
        return bytes(_frame().to_json(orient="records", lines=True).encode())
    buffer = io.BytesIO()
    _frame().to_parquet(buffer, index=False)
    return buffer.getvalue()


class _Store:
    """One canned object behind all three client shapes, counting clients and closes."""

    def __init__(self, content: bytes) -> None:
        self.content = content
        self.built = 0
        self.closed = 0
        self.fail_reads = False

    def _slice(self, start: int, length: int | None) -> bytes:
        if self.fail_reads:
            raise RuntimeError("store unreachable")
        end = len(self.content) if length is None else start + length
        return self.content[start:end]

    # ── the client a factory returns ─────────────────────────────────────
    def client(self, *_args: Any) -> _Store:
        self.built += 1
        return self

    def close(self) -> None:
        self.closed += 1

    # ── boto3 S3 ─────────────────────────────────────────────────────────
    def head_object(self, **_kw: Any) -> dict[str, Any]:
        return {"ContentLength": len(self.content), "LastModified": _LANDED}

    def get_object(self, *, Range: str, **_kw: Any) -> dict[str, Any]:  # noqa: N803 — boto3
        first, _, last = Range.removeprefix("bytes=").partition("-")
        return {"Body": io.BytesIO(self._slice(int(first), int(last) - int(first) + 1))}

    # ── azure BlobServiceClient ──────────────────────────────────────────
    def get_blob_client(self, *, container: str, blob: str) -> Any:
        return SimpleNamespace(
            get_blob_properties=lambda: SimpleNamespace(
                size=len(self.content), last_modified=_LANDED
            ),
            download_blob=lambda offset=0, length=None: SimpleNamespace(
                readall=lambda: self._slice(offset or 0, length)
            ),
        )

    # ── google storage.Client ────────────────────────────────────────────
    def bucket(self, _name: str, user_project: str | None = None) -> Any:
        def _download(*, start: int = 0, end: int | None = None, **_kw: Any) -> bytes:
            return self._slice(start, None if end is None else end - start + 1)

        blob = SimpleNamespace(
            size=len(self.content),
            time_created=_LANDED,
            updated=_LANDED,
            reload=lambda **_kw: None,
            download_as_bytes=_download,
        )
        return SimpleNamespace(blob=lambda _path: blob)


def _install(monkeypatch: pytest.MonkeyPatch, conn_type: str, fmt: str) -> _Store:
    store = _Store(_content(fmt))
    monkeypatch.setattr(flatfile, _FACTORIES[conn_type], store.client)
    return store


def _conn(conn_type: str) -> Any:
    return SimpleNamespace(
        id=uuid.uuid4(), type=conn_type, config=_CONFIGS[conn_type], secret_ref="ref"
    )


_EVERY = [(t, f) for t in _FACTORIES for f in ("csv", "parquet", "json")]


@pytest.mark.parametrize(("conn_type", "fmt"), _EVERY)
def test_suggest_reads_the_secret_and_builds_the_store_client_once(
    db_session: Any, monkeypatch: pytest.MonkeyPatch, conn_type: str, fmt: str
) -> None:
    store = _install(monkeypatch, conn_type, fmt)
    secrets = FakeSecretStore({"ref": "s"})

    suggestion = svc.suggest_policy_for_target(
        _conn(conn_type), session=db_session, path=f"orders/a.{fmt}", secret_store=secrets
    )

    assert suggestion  # the header and the sample were both really read
    assert secrets.requested == ["ref"]
    assert store.built == 1
    assert store.closed == 1  # released when the suggest ends, not left to the GC


@pytest.mark.parametrize(("conn_type", "fmt"), _EVERY)
def test_outside_a_suggest_each_call_builds_and_closes_its_own_client(
    db_session: Any, monkeypatch: pytest.MonkeyPatch, conn_type: str, fmt: str
) -> None:
    # The memo is scoped: a later request must not reuse a client or a rotated secret.
    store = _install(monkeypatch, conn_type, fmt)
    secrets = FakeSecretStore({"ref": "s"})
    conn = _conn(conn_type)
    path = f"orders/a.{fmt}"

    columns = svc.list_columns(conn, session=db_session, path=path, secret_store=secrets)
    svc.profile_connection(
        conn, session=db_session, columns=columns, top_n=3, path=path, secret_store=secrets
    )

    assert columns == ["id", "email"]
    assert secrets.requested == ["ref", "ref"]
    assert (store.built, store.closed) == (2, 2)


def test_two_connections_in_one_scope_do_not_share_a_client(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _install(monkeypatch, "s3", "csv")
    secrets = FakeSecretStore({"ref": "s"})
    with svc.shared_connection():
        for _ in range(2):
            svc.list_columns(
                _conn("s3"), session=db_session, path="orders/a.csv", secret_store=secrets
            )
    assert (store.built, store.closed) == (2, 2)


def test_a_failed_read_does_not_leave_its_client_for_the_next_call(
    db_session: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _install(monkeypatch, "s3", "csv")
    secrets = FakeSecretStore({"ref": "s"})
    conn = _conn("s3")
    with svc.shared_connection():
        store.fail_reads = True
        with pytest.raises(ProfileFailedError):
            svc.list_columns(conn, session=db_session, path="orders/a.csv", secret_store=secrets)
        assert (store.built, store.closed) == (1, 1)  # dropped and closed, not kept
        store.fail_reads = False
        assert svc.list_columns(
            conn, session=db_session, path="orders/a.csv", secret_store=secrets
        ) == ["id", "email"]
    assert (store.built, store.closed) == (2, 2)
    assert secrets.requested == ["ref", "ref"]
