"""Interactive datasource browsing (#466) — the service and the flat-file listing seam."""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from azure.storage.blob import BlobPrefix, BlobProperties

from backend.app.datasources import flatfile
from backend.app.db.models import Connection
from backend.app.services import browse_service, credential_health
from backend.app.services.browse_service import (
    BrowseFailedError,
    BrowseInputInvalidError,
    BrowseUnsupportedError,
)

_UC_CONFIG = {"workspace_url": "https://dbc-1.cloud.databricks.com", "warehouse_id": "abc"}
_S3_CONFIG = {"bucket": "landing", "region": "us-east-1", "access_key_id": "AKIAEXAMPLE"}
_ADLS_CONFIG = {"account_url": "https://acct.blob.core.windows.net", "container": "raw"}
_SECRET = "s3cr3t-value-never-returned"


class _Store:
    def get(self, name: str) -> str:
        return _SECRET


class _Result:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[Any, ...]]:
        return self._rows


class _FakeConn:
    """Records every statement + params and answers with canned rows."""

    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, clause: Any, params: dict[str, Any] | None = None) -> _Result:
        self.calls.append((str(clause), dict(params or {})))
        return _Result(self.rows)


def _conn(conn_type: str, config: dict[str, Any], *, secret_ref: str | None = "ref") -> Connection:
    return Connection(
        id=uuid.uuid4(),
        name="c",
        type=conn_type,
        env="dev",
        config=dict(config),
        secret_ref=secret_ref,
    )


@pytest.fixture(autouse=True)
def _no_health(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        credential_health, "credential_use", lambda *_a, **_k: contextlib.nullcontext()
    )


@pytest.fixture
def fake_sql(monkeypatch: pytest.MonkeyPatch) -> _FakeConn:
    fake = _FakeConn([])

    @contextlib.contextmanager
    def _open(connection: Connection, store: Any) -> Iterator[_FakeConn]:
        yield fake

    monkeypatch.setattr(browse_service, "_open_connection", _open)
    return fake


def _catalog(conn: Connection, **kw: Any) -> browse_service.CatalogListing:
    kw.setdefault("catalog", None)
    kw.setdefault("schema", None)
    kw.setdefault("limit", 10)
    return browse_service.browse_catalog(conn, session=None, secret_store=_Store(), **kw)  # type: ignore[arg-type]


# ───────────────────────── prefix validation ─────────────────────────


@pytest.mark.parametrize(
    "prefix",
    ["", "raw/", "raw/2026/", "raw/orders_", "a b/ünïcode/", "x/y.z/", "._hidden/", "..x/"],
)
def test_ordinary_prefixes_are_accepted_verbatim(prefix: str) -> None:
    assert browse_service.validate_prefix(prefix) == prefix


@pytest.mark.parametrize(
    "prefix",
    [
        "\x00",
        "raw/\x00/",
        "raw\n/",
        "raw\t",
        "raw\x7f/",
        "raw\\sub/",
        "/raw/",
        "../",
        "raw/../secret/",
        "raw/./",
        ".",
        "..",
        "raw//x/",
        "raw/..",
    ],
)
def test_traversal_and_control_characters_are_rejected(prefix: str) -> None:
    with pytest.raises(BrowseInputInvalidError) as exc:
        browse_service.validate_prefix(prefix)
    assert exc.value.status_code == 422


# ───────────────────────── catalog browse ─────────────────────────


def test_top_level_lists_catalogs_with_bound_limit_plus_one(fake_sql: _FakeConn) -> None:
    fake_sql.rows = [("dataq_retail",), ("workspace",)]
    listing = _catalog(_conn("unity_catalog", _UC_CONFIG), limit=10)
    assert listing.level == "catalog"
    assert [e.name for e in listing.entries] == ["dataq_retail", "workspace"]
    assert listing.truncated is False
    sql, params = fake_sql.calls[0]
    assert "system.information_schema.catalogs" in sql
    assert "NOT IN ('system', 'samples', '__databricks_internal')" in sql
    assert params == {"lim": 11}


def test_schema_level_binds_the_catalog_rather_than_interpolating_it(fake_sql: _FakeConn) -> None:
    fake_sql.rows = [("gold",), ("silver",)]
    listing = _catalog(_conn("unity_catalog", _UC_CONFIG), catalog="dataq_retail")
    assert listing.level == "schema"
    sql, params = fake_sql.calls[0]
    assert "system.information_schema.schemata" in sql
    assert "dataq_retail" not in sql
    assert params["catalog"] == "dataq_retail"
    assert "schema_name != 'information_schema'" in sql


def test_table_level_reuses_the_inventory_enumeration_query(fake_sql: _FakeConn) -> None:
    fake_sql.rows = [("dataq_retail", "gold", "daily_revenue"), ("dataq_retail", "gold", None)]
    listing = _catalog(_conn("unity_catalog", _UC_CONFIG), catalog="dataq_retail", schema="gold")
    assert listing.level == "table"
    assert [e.name for e in listing.entries] == ["daily_revenue"]  # NULL row dropped
    sql, params = fake_sql.calls[0]
    # The ADR 0040 query and its exclusions, narrowed by bound filters.
    assert "system.information_schema.tables" in sql
    assert "'STREAMING_TABLE'" in sql
    assert "table_catalog = :catalog" in sql and "table_schema = :schema" in sql
    assert params == {"catalog": "dataq_retail", "schema": "gold", "lim": 11}


def test_a_page_of_exactly_limit_is_complete_and_one_more_is_truncated(
    fake_sql: _FakeConn,
) -> None:
    conn = _conn("unity_catalog", _UC_CONFIG)
    fake_sql.rows = [(f"c{i}",) for i in range(3)]
    exact = _catalog(conn, limit=3)
    assert (len(exact.entries), exact.truncated) == (3, False)

    fake_sql.rows = [(f"c{i}",) for i in range(4)]
    over = _catalog(conn, limit=3)
    assert [e.name for e in over.entries] == ["c0", "c1", "c2"]
    assert over.truncated is True


def test_names_dataq_cannot_target_are_listed_but_not_selectable(fake_sql: _FakeConn) -> None:
    fake_sql.rows = [("good_one",), ("has-hyphen",), ("with space",)]
    listing = _catalog(_conn("unity_catalog", _UC_CONFIG))
    assert [(e.name, e.selectable) for e in listing.entries] == [
        ("good_one", True),
        ("has-hyphen", False),
        ("with space", False),
    ]


@pytest.mark.parametrize(
    ("catalog", "schema"),
    [
        ("dataq_retail'; DROP TABLE x;--", None),
        ("dataq_retail", "gold`"),
        ("a.b", None),
        ("", None),
        ("dataq\x00retail", None),
        ("dataq_retail", "../gold"),
        ("1starts_with_digit", None),
    ],
)
def test_malformed_identifiers_are_422_before_any_query(
    fake_sql: _FakeConn, catalog: str, schema: str | None
) -> None:
    with pytest.raises(BrowseInputInvalidError):
        _catalog(_conn("unity_catalog", _UC_CONFIG), catalog=catalog, schema=schema)
    assert fake_sql.calls == []


def test_a_schema_without_a_catalog_is_refused(fake_sql: _FakeConn) -> None:
    with pytest.raises(BrowseInputInvalidError):
        _catalog(_conn("unity_catalog", _UC_CONFIG), schema="gold")
    assert fake_sql.calls == []


@pytest.mark.parametrize("conn_type", ["s3", "adls_gen2", "adf", "airflow", "dbt"])
def test_catalog_browse_refuses_non_table_types(fake_sql: _FakeConn, conn_type: str) -> None:
    with pytest.raises(BrowseUnsupportedError) as exc:
        _catalog(_conn(conn_type, {}))
    assert exc.value.status_code == 422
    assert fake_sql.calls == []


_SF_CONFIG = {"account": "acct", "user": "u", "database": "DB", "warehouse": "WH"}
_ICE_CONFIG = {"catalog_name": "harness", "catalog_type": "sql", "catalog_uri": "sqlite://"}


def test_snowflake_lists_schemas_then_binds_the_schema_for_tables(fake_sql: _FakeConn) -> None:
    fake_sql.rows = [("RETAIL",), ("ANALYTICS",)]
    listing = _catalog(_conn("snowflake", _SF_CONFIG), limit=5)
    assert listing.level == "schema"
    sql, params = fake_sql.calls[0]
    assert "INFORMATION_SCHEMA.SCHEMATA" in sql and params == {"lim": 6}

    fake_sql.rows = [("ORDERS",)]
    listing = _catalog(_conn("snowflake", _SF_CONFIG), schema="RETAIL", limit=5)
    assert listing.level == "table"
    assert [e.name for e in listing.entries] == ["ORDERS"]
    sql, params = fake_sql.calls[1]
    assert "table_schema = :schema" in sql and "RETAIL" not in sql
    assert params == {"schema": "RETAIL", "lim": 6}


@pytest.mark.parametrize(("given", "bound"), [("retail", "RETAIL"), ("Retail", "Retail")])
def test_snowflake_folds_a_lower_case_schema_like_a_run_does(
    fake_sql: _FakeConn, given: str, bound: str
) -> None:
    _catalog(_conn("snowflake", _SF_CONFIG), schema=given)
    assert fake_sql.calls[0][1]["schema"] == bound


@pytest.mark.parametrize("conn_type", ["snowflake", "iceberg"])
def test_schema_rooted_types_refuse_a_catalog(fake_sql: _FakeConn, conn_type: str) -> None:
    with pytest.raises(BrowseInputInvalidError) as exc:
        _catalog(
            _conn(conn_type, _SF_CONFIG if conn_type == "snowflake" else _ICE_CONFIG), catalog="X"
        )
    assert exc.value.detail == {"field": "catalog"}
    assert fake_sql.calls == []


class _FakeIcebergCatalog:
    def __init__(self) -> None:
        self.listed: list[str] = []

    def list_namespaces(self) -> list[tuple[str, ...]]:
        return [("retail",), ("finance",), ("retail",)]

    def list_tables(self, namespace: str) -> list[tuple[str, ...]]:
        self.listed.append(namespace)
        return [(namespace, "purchase_orders"), (namespace, "invoices")]


@pytest.fixture
def fake_iceberg(monkeypatch: pytest.MonkeyPatch) -> _FakeIcebergCatalog:
    from backend.app.datasources import iceberg

    fake = _FakeIcebergCatalog()
    monkeypatch.setattr(iceberg, "iceberg_credentials", lambda *_a: (None, None))
    monkeypatch.setattr(iceberg, "load_iceberg_catalog", lambda *_a: fake)
    return fake


def test_iceberg_lists_top_level_namespaces_then_their_tables(
    fake_iceberg: _FakeIcebergCatalog,
) -> None:
    # No secret_ref: an Iceberg catalog may need none, so browsing must not demand one.
    conn = _conn("iceberg", _ICE_CONFIG, secret_ref=None)
    listing = _catalog(conn)
    assert listing.level == "schema"
    assert [e.name for e in listing.entries] == ["finance", "retail"]

    listing = _catalog(conn, schema="retail", limit=1)
    assert listing.level == "table"
    assert [e.name for e in listing.entries] == ["invoices"]
    assert listing.truncated is True
    assert fake_iceberg.listed == ["retail"]


def test_an_iceberg_catalog_failure_is_502_without_the_driver_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.datasources import iceberg

    def _boom(*_a: Any) -> Any:
        raise RuntimeError(f"catalog down password={_SECRET}")

    monkeypatch.setattr(iceberg, "iceberg_credentials", lambda *_a: (None, None))
    monkeypatch.setattr(iceberg, "load_iceberg_catalog", _boom)
    with pytest.raises(BrowseFailedError) as exc:
        _catalog(_conn("iceberg", _ICE_CONFIG))
    assert _SECRET not in f"{exc.value.message} {exc.value.detail}"


def test_a_connection_without_a_credential_is_refused(fake_sql: _FakeConn) -> None:
    with pytest.raises(BrowseInputInvalidError):
        _catalog(_conn("unity_catalog", _UC_CONFIG, secret_ref=None))
    assert fake_sql.calls == []


def test_a_warehouse_failure_is_502_without_the_driver_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @contextlib.contextmanager
    def _boom(connection: Connection, store: Any) -> Iterator[Any]:
        raise RuntimeError(f"connect failed token={_SECRET} host=internal.example")
        yield

    monkeypatch.setattr(browse_service, "_open_connection", _boom)
    with pytest.raises(BrowseFailedError) as exc:
        _catalog(_conn("unity_catalog", _UC_CONFIG))
    assert exc.value.status_code == 502
    rendered = f"{exc.value.message} {exc.value.detail}"
    assert _SECRET not in rendered and "internal.example" not in rendered
    assert exc.value.detail["reason"]


# ───────────────────────── flat-file listing seam ─────────────────────────


class _FakeS3:
    def __init__(self, page: dict[str, Any]) -> None:
        self.page = page
        self.kwargs: dict[str, Any] = {}

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        self.kwargs = kwargs
        return self.page


_TS = datetime(2026, 9, 1, tzinfo=UTC)


def _s3_listing(
    monkeypatch: pytest.MonkeyPatch, page: dict[str, Any], *, prefix: str = "", limit: int = 10
) -> tuple[_FakeS3, flatfile.DirectoryListing]:
    fake = _FakeS3(page)
    monkeypatch.setattr(flatfile, "_s3_client", lambda cfg, secret: fake)
    listing = flatfile.list_directory(
        conn_type="s3", config=_S3_CONFIG, prefix=prefix, secret=_SECRET, limit=limit
    )
    return fake, listing


def test_s3_listing_is_one_delimited_request_of_limit_plus_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake, listing = _s3_listing(
        monkeypatch,
        {
            "CommonPrefixes": [{"Prefix": "raw/a/"}, {"Prefix": "raw/b/"}],
            "Contents": [
                {"Key": "raw/", "Size": 0},  # the folder's own marker object
                {"Key": "raw/x.csv", "Size": 12, "LastModified": _TS},
            ],
            "IsTruncated": False,
        },
        prefix="raw/",
        limit=5,
    )
    assert fake.kwargs == {"Bucket": "landing", "Prefix": "raw/", "Delimiter": "/", "MaxKeys": 6}
    assert listing.folders == ["raw/a/", "raw/b/"]
    assert [(f.path, f.size, f.last_modified) for f in listing.files] == [("raw/x.csv", 12, _TS)]
    assert listing.truncated is False


def test_s3_truncation_is_reported_from_the_store_and_from_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, from_store = _s3_listing(
        monkeypatch, {"Contents": [{"Key": "a.csv"}], "IsTruncated": True}, limit=5
    )
    assert from_store.truncated is True

    _, from_cap = _s3_listing(
        monkeypatch,
        {
            "CommonPrefixes": [{"Prefix": "b/"}],
            "Contents": [{"Key": "a.csv"}, {"Key": "c.csv"}],
            "IsTruncated": False,
        },
        limit=2,
    )
    # Store order is lexicographic across both kinds; the cap keeps that order.
    assert from_cap.folders == ["b/"]
    assert [f.path for f in from_cap.files] == ["a.csv"]
    assert from_cap.truncated is True


def _blob(name: str, size: int = 1) -> BlobProperties:
    props = BlobProperties()
    props.name = name
    props.size = size
    props.last_modified = _TS
    return props


def _prefix(name: str) -> BlobPrefix:
    item = BlobPrefix.__new__(BlobPrefix)
    item.name = name
    return item


class _FakeContainer:
    def __init__(self, items: list[Any]) -> None:
        self.items = items
        self.kwargs: dict[str, Any] = {}
        self.pulled = 0

    def walk_blobs(self, **kwargs: Any) -> Iterator[Any]:
        self.kwargs = kwargs
        for item in self.items:
            self.pulled += 1
            yield item


class _FakeService:
    def __init__(self, container: _FakeContainer) -> None:
        self.container = container
        self.closed = False
        self.asked_for: str | None = None

    def get_container_client(self, name: str) -> _FakeContainer:
        self.asked_for = name
        return self.container

    def close(self) -> None:
        self.closed = True


def _adls_listing(
    monkeypatch: pytest.MonkeyPatch, items: list[Any], *, prefix: str = "", limit: int = 10
) -> tuple[_FakeService, flatfile.DirectoryListing]:
    service = _FakeService(_FakeContainer(items))
    monkeypatch.setattr(flatfile, "_blob_service", lambda cfg, secret: service)
    listing = flatfile.list_directory(
        conn_type="adls_gen2", config=_ADLS_CONFIG, prefix=prefix, secret=_SECRET, limit=limit
    )
    return service, listing


def test_adls_listing_walks_one_level_and_closes_the_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, listing = _adls_listing(
        monkeypatch,
        # An HNS account lists a directory both as a prefix and as a zero-byte blob.
        [_prefix("raw/a/"), _blob("raw/a", 0), _blob("raw/x.parquet", 9)],
        prefix="raw/",
    )
    assert service.asked_for == "raw"
    assert service.container.kwargs == {
        "name_starts_with": "raw/",
        "delimiter": "/",
        "results_per_page": 11,
    }
    assert listing.folders == ["raw/a/"]
    assert [f.path for f in listing.files] == ["raw/x.parquet"]
    assert listing.truncated is False
    assert service.closed is True


def test_adls_listing_never_pulls_past_limit_plus_one(monkeypatch: pytest.MonkeyPatch) -> None:
    items = [_blob(f"f{i:03}.csv") for i in range(50)]
    service, listing = _adls_listing(monkeypatch, items, limit=3)
    assert service.container.pulled == 4
    assert [f.path for f in listing.files] == ["f000.csv", "f001.csv", "f002.csv"]
    assert listing.truncated is True


def test_adls_placeholder_blob_does_not_make_a_complete_level_read_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = [_blob("data/", 0)] + [_blob(f"data/f{i}.csv") for i in range(3)]
    _, listing = _adls_listing(monkeypatch, items, prefix="data/", limit=3)
    assert [f.path for f in listing.files] == ["data/f0.csv", "data/f1.csv", "data/f2.csv"]
    assert listing.truncated is False


def test_adls_hns_directory_pairs_count_once_toward_the_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items: list[Any] = []
    for name in ("a", "b", "c"):
        items += [_blob(name, 0), _prefix(f"{name}/")]
    _, complete = _adls_listing(monkeypatch, items, limit=3)
    assert (complete.folders, complete.files, complete.truncated) == (["a/", "b/", "c/"], [], False)

    _, over = _adls_listing(monkeypatch, [*items, _blob("d", 0), _prefix("d/")], limit=3)
    assert over.folders == ["a/", "b/", "c/"]
    assert over.truncated is True


def test_adls_root_listing_passes_no_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    service, _ = _adls_listing(monkeypatch, [])
    assert service.container.kwargs["name_starts_with"] is None


# ───────────────────────── file browse service ─────────────────────────


def test_browse_files_names_the_root_and_carries_the_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def _list(**kwargs: Any) -> flatfile.DirectoryListing:
        seen.update(kwargs)
        return flatfile.DirectoryListing(folders=["raw/"], files=[], truncated=True)

    monkeypatch.setattr(flatfile, "list_directory", _list)
    listing = browse_service.browse_files(
        _conn("s3", _S3_CONFIG), session=None, prefix="", limit=7, secret_store=_Store()  # type: ignore[arg-type]
    )
    assert (listing.root, listing.folders, listing.truncated, listing.limit) == (
        "landing",
        ["raw/"],
        True,
        7,
    )
    assert seen["secret"] == _SECRET and seen["limit"] == 7


@pytest.mark.parametrize("conn_type", ["unity_catalog", "snowflake", "iceberg", "adf"])
def test_file_browse_is_flat_file_only(conn_type: str) -> None:
    with pytest.raises(BrowseUnsupportedError):
        browse_service.browse_files(
            _conn(conn_type, {}), session=None, prefix="", limit=5, secret_store=_Store()  # type: ignore[arg-type]
        )


def test_a_store_failure_is_502_without_the_sdk_message(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(**_kw: Any) -> flatfile.DirectoryListing:
        raise RuntimeError(f"AuthenticationFailed sig={_SECRET}")

    monkeypatch.setattr(flatfile, "list_directory", _boom)
    with pytest.raises(BrowseFailedError) as exc:
        browse_service.browse_files(
            _conn("adls_gen2", _ADLS_CONFIG),
            session=None,  # type: ignore[arg-type]
            prefix="",
            limit=5,
            secret_store=_Store(),  # type: ignore[arg-type]
        )
    assert _SECRET not in f"{exc.value.message} {exc.value.detail}"


def test_a_traversal_prefix_never_reaches_the_store(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def _list(**_kw: Any) -> flatfile.DirectoryListing:
        nonlocal called
        called = True
        return flatfile.DirectoryListing(folders=[], files=[], truncated=False)

    monkeypatch.setattr(flatfile, "list_directory", _list)
    with pytest.raises(BrowseInputInvalidError):
        browse_service.browse_files(
            _conn("s3", _S3_CONFIG), session=None, prefix="a/../b/", limit=5, secret_store=_Store()  # type: ignore[arg-type]
        )
    assert called is False
