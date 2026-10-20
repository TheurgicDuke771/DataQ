"""Google Cloud Storage flat files against a real `fake-gcs-server` (#1683).

Opt-in: ``GCS_EMULATOR_URL=http://127.0.0.1:4443`` (``docker run --rm -p 127.0.0.1:4443:4443
fsouza/fake-gcs-server -scheme http -port 4443 -external-url http://127.0.0.1:4443``). Every
value here crosses the driver boundary — object size, arrival time, the 404 shape, range reads,
delimited listings — so the stubs in tests/datasources/test_flatfile.py are checked against
what `google-cloud-storage` really returns. Real GCS is not reachable from CI (#2224); the
service-account path is exercised against the emulator with a locally minted key whose
``token_uri`` is a stub that issues a bearer, so the whole google-auth flow runs for real.
"""

from __future__ import annotations

import io
import json
import os
import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pandas as pd
import pytest
from google.cloud import storage

from backend.app.datasources import flatfile
from backend.app.datasources.base import CheckSpec, MonitorSpec
from backend.app.datasources.gcs import GcsConfig, GcsConnectionAdapter, gcs_client
from backend.app.datasources.registry import build_check_runner, resolve_target_shape
from backend.app.services.asset_identity import resolve_asset_identity
from backend.tests.support.adversarial import ADVERSARIAL_CSV_BODIES
from backend.tests.support.fake_secret_store import FakeSecretStore

_URL = os.environ.get("GCS_EMULATOR_URL", "").rstrip("/")
pytestmark = pytest.mark.skipif(not _URL, reason="requires GCS_EMULATOR_URL (fake-gcs-server)")

_CSV = (
    b"id,amount,ts\n1,10.5,2026-01-01T00:00:00\n2,20.0,2026-01-02T00:00:00\n"
    b"3,,2026-01-03T00:00:00\n"
)


class _TokenStub(BaseHTTPRequestHandler):
    """The OAuth token endpoint a key file's ``token_uri`` names: hands out one bearer."""

    grants: list[bytes] = []  # noqa: RUF012 — reset per fixture

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        _TokenStub.grants.append(body)
        out = json.dumps({"access_token": "stub", "expires_in": 3600, "token_type": "Bearer"})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out.encode())

    def log_message(self, *_: Any) -> None:
        pass


@pytest.fixture(scope="module")
def token_uri() -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), _TokenStub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/token"
    finally:
        server.shutdown()


@pytest.fixture(scope="module")
def key_file(token_uri: str) -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    pem = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )
    return json.dumps(
        {
            "type": "service_account",
            "project_id": "dq-emulator",
            "private_key_id": "k1",
            "private_key": pem,
            "client_email": "dq@dq-emulator.iam.gserviceaccount.com",
            "client_id": "1",
            "token_uri": token_uri,
        }
    )


@pytest.fixture(scope="module")
def bucket() -> Iterator[str]:
    """A bucket seeded with two dated CSVs, a Parquet file and a sub-folder."""
    name = f"dq-{uuid.uuid4().hex[:10]}"
    admin = gcs_client(GcsConfig(bucket=name, auth_type="anonymous", endpoint_url=_URL), None)
    admin.create_bucket(name)
    b = admin.bucket(name)
    b.blob("orders/orders_2026-01-01.csv").upload_from_string(_CSV)
    b.blob("orders/orders_2026-01-02.csv").upload_from_string(_CSV.replace(b"3,,", b"3,30,"))
    buf = io.BytesIO()
    pd.DataFrame({"id": [1, 2, 3, 4], "amount": [1.5, 2.5, None, 4.5]}).to_parquet(buf)
    b.blob("orders/orders_2026-01-03.parquet").upload_from_string(buf.getvalue())
    b.blob("orders/archive/old.csv").upload_from_string(b"a\n1\n")
    for label, body in ADVERSARIAL_CSV_BODIES:
        b.blob(f"adversarial/{label}.csv").upload_from_string(body)
    admin.close()
    yield name


def _config(bucket: str, **overrides: Any) -> dict[str, Any]:
    return {"bucket": bucket, "auth_type": "service_account_key", "endpoint_url": _URL, **overrides}


# ───────────────────────── adapter.test ─────────────────────────


def test_the_connection_test_passes_with_the_key_file_and_anonymously(
    bucket: str, key_file: str
) -> None:
    _TokenStub.grants.clear()
    GcsConnectionAdapter().test(_config(bucket), key_file)
    # The signed JWT grant went to the key's own token endpoint — the google-auth flow ran.
    assert _TokenStub.grants and b"grant_type=urn%3Aietf%3Aparams%3Aoauth" in _TokenStub.grants[0]
    GcsConnectionAdapter().test(_config(bucket, auth_type="anonymous"), None)


def test_a_missing_bucket_fails_the_connection_test(key_file: str) -> None:
    from google.api_core.exceptions import NotFound

    with pytest.raises(NotFound):
        GcsConnectionAdapter().test(_config("no-such-bucket-" + uuid.uuid4().hex[:6]), key_file)


# ───────────────────────── the flat-file seams, for real ─────────────────────────


def test_file_stat_reads_size_and_arrival_time_and_maps_the_404(bucket: str, key_file: str) -> None:
    stat = flatfile.file_stat(
        conn_type="gcs",
        config=_config(bucket),
        path="orders/orders_2026-01-01.csv",
        secret=key_file,
    )
    assert stat.size == len(_CSV)
    assert isinstance(stat.last_modified, datetime) and stat.last_modified.tzinfo is not None
    assert datetime.now(UTC) - stat.last_modified < timedelta(minutes=10)
    missing = flatfile.file_stat(
        conn_type="gcs", config=_config(bucket), path="orders/nope.csv", secret=key_file
    )
    assert missing == flatfile.FileStat()


def test_range_reads_download_and_listing(bucket: str, key_file: str) -> None:
    cfg = _config(bucket)
    assert (
        flatfile.read_range(
            conn_type="gcs",
            config=cfg,
            path="orders/orders_2026-01-01.csv",
            secret=key_file,
            start=3,
            length=6,
        )
        == _CSV[3:9]
    )
    assert (
        flatfile.download_bytes(
            conn_type="gcs", config=cfg, path="orders/orders_2026-01-01.csv", secret=key_file
        )
        == _CSV
    )
    refs = flatfile.list_files(
        conn_type="gcs", config=cfg, prefix="orders/orders_", secret=key_file
    )
    assert [r.path for r in refs] == [
        "orders/orders_2026-01-01.csv",
        "orders/orders_2026-01-02.csv",
        "orders/orders_2026-01-03.parquet",
    ]
    assert all(r.last_modified is not None for r in refs)


def test_browse_lists_one_level_with_folders(bucket: str, key_file: str) -> None:
    listing = flatfile.list_directory(
        conn_type="gcs", config=_config(bucket), prefix="orders/", secret=key_file, limit=10
    )
    assert listing.folders == ["orders/archive/"]
    assert [f.path for f in listing.files] == [
        "orders/orders_2026-01-01.csv",
        "orders/orders_2026-01-02.csv",
        "orders/orders_2026-01-03.parquet",
    ]
    assert all(f.size and f.last_modified for f in listing.files)
    assert not listing.truncated
    capped = flatfile.list_directory(
        conn_type="gcs", config=_config(bucket), prefix="orders/", secret=key_file, limit=2
    )
    assert capped.truncated and len(capped.folders) + len(capped.files) == 2


def test_batch_resolution_picks_the_latest_key(bucket: str, key_file: str) -> None:
    resolved = resolve_target_shape(
        "gcs", {"pattern": r"orders_(\d{4}-\d{2}-\d{2})\.csv", "prefix": "orders/"}
    )
    assert resolved.batch is not None
    path = flatfile.resolve_batch_file(
        conn_type="gcs",
        config=_config(bucket),
        secret=key_file,
        prefix=resolved.batch.prefix,
        pattern=resolved.batch.pattern,
    )
    assert path == "orders/orders_2026-01-02.csv"


def test_csv_and_parquet_read_into_frames(bucket: str, key_file: str) -> None:
    csv = flatfile.read_dataframe(
        conn_type="gcs",
        config=_config(bucket),
        path="orders/orders_2026-01-01.csv",
        secret=key_file,
    )
    assert list(csv.columns) == ["id", "amount", "ts"] and len(csv) == 3
    pq = flatfile.read_dataframe(
        conn_type="gcs",
        config=_config(bucket),
        path="orders/orders_2026-01-03.parquet",
        secret=key_file,
    )
    assert len(pq) == 4 and pq["amount"].isna().sum() == 1


@pytest.mark.parametrize(("label", "body"), ADVERSARIAL_CSV_BODIES)
def test_bounded_csv_reads_match_a_whole_buffer_parse(
    bucket: str, key_file: str, label: str, body: bytes
) -> None:
    head = flatfile.read_csv_head(
        conn_type="gcs",
        config=_config(bucket),
        path=f"adversarial/{label}.csv",
        secret=key_file,
        rows=100,
    )
    whole = flatfile.read_csv_bytes(io.BytesIO(body))
    pd.testing.assert_frame_equal(
        head.reset_index(drop=True), whole.head(100).reset_index(drop=True)
    )


# ───────────────────────── a suite run: volume + arrival freshness ─────────────────────────


def test_the_runner_runs_a_volume_check_and_arrival_time_freshness(
    bucket: str, key_file: str
) -> None:
    store = FakeSecretStore({"gcs-key": key_file})
    runner = build_check_runner(
        conn_type="gcs", config=_config(bucket), secret_ref="gcs-key", secret_store=store
    )
    assert isinstance(runner, flatfile.FlatFileCheckRunner)
    volume, freshness = runner.run_monitors(
        table="orders/orders_2026-01-01.csv",
        schema=None,
        monitors=[
            MonitorSpec(kind="volume", config={"min_rows": 1, "max_rows": 10}),
            # No column: the object's own arrival time (#940).
            MonitorSpec(kind="freshness", config={}),
        ],
    )
    assert volume.success and not volume.errored and volume.metric_value == 0.0
    assert volume.observed_value is not None and volume.observed_value["row_count"] == 3
    assert not freshness.errored and freshness.metric_value is not None
    assert freshness.metric_value < 1.0  # hours since the upload moments ago
    assert freshness.expected_value == {"monitor": "freshness", "source": "file_modified_time"}

    suite = runner.run_checks(
        table="orders/orders_2026-01-01.csv",
        schema=None,
        checks=[
            CheckSpec(
                expectation_type="expect_column_values_to_not_be_null",
                kwargs={"column": "amount"},
            )
        ],
    )
    (outcome,) = suite.checks
    assert not outcome.success and not outcome.errored
    assert outcome.expected_value is not None  # one null of three rows failed the check


def test_anonymous_runner_needs_no_secret(bucket: str) -> None:
    runner = build_check_runner(
        conn_type="gcs",
        config=_config(bucket, auth_type="anonymous"),
        secret_ref=None,
        secret_store=FakeSecretStore(),
    )
    assert isinstance(runner, flatfile.FlatFileCheckRunner)
    (outcome,) = runner.run_monitors(
        table="orders/orders_2026-01-02.csv",
        schema=None,
        monitors=[MonitorSpec(kind="volume", config={"min_rows": 3, "max_rows": 3})],
    )
    assert outcome.success and not outcome.errored


def test_asset_identity_carries_the_emulator_authority(bucket: str) -> None:
    identity = resolve_asset_identity("gcs", _config(bucket), {"path": "orders/a.csv"})
    host = _URL.removeprefix("http://").removeprefix("https://")
    assert identity.namespace == f"gs://{host}/{bucket}"


def test_the_client_the_seams_build_is_the_real_one(bucket: str, key_file: str) -> None:
    client = gcs_client(GcsConfig.model_validate(_config(bucket)), key_file)
    try:
        assert isinstance(client, storage.Client)
        blob = client.bucket(bucket).get_blob("orders/orders_2026-01-01.csv")
        assert blob is not None and isinstance(blob.size, int)
        assert isinstance(blob.updated, datetime)
    finally:
        client.close()
