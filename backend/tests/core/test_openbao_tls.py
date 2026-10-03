"""The OpenBao client against an HTTPS vault whose certificate chains to a private CA (#2338).

A real TLS handshake against a local server, not a mocked transport: what is under test is
which certificates the client trusts.
"""

from __future__ import annotations

import json
import ssl
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from backend.app.core.config import Settings
from backend.app.core.secrets import _KV_FIELD, OpenBaoSecretStore, SecretStoreUnavailableError
from backend.scripts import local_ca

_VALUE = "value-held-by-the-vault"


class _KvHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = json.dumps({"data": {"data": {_KV_FIELD: _VALUE}}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:  # keep test output clean
        return


@pytest.fixture
def https_vault(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    """`(address, CA certificate)` of an HTTPS server with a `localhost` leaf from a private CA."""
    local_ca.ensure(tmp_path / "certs", tmp_path / "ca-key")
    server = HTTPServer(("127.0.0.1", 0), _KvHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(
        tmp_path / "certs" / "localhost.pem", tmp_path / "certs" / "localhost.key"
    )
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://localhost:{server.server_address[1]}", tmp_path / "certs" / "ca.pem"
    finally:
        server.shutdown()
        server.server_close()


def test_a_private_ca_bundle_lets_the_client_reach_an_https_vault(
    https_vault: tuple[str, Path],
) -> None:
    addr, ca = https_vault
    store = OpenBaoSecretStore(addr, "token", ca_bundle=str(ca))

    assert store.get("conn-x") == _VALUE


def test_without_the_bundle_the_same_vault_is_not_trusted(https_vault: tuple[str, Path]) -> None:
    """The bundle is what makes the connection possible; the system store does not know this CA."""
    addr, _ = https_vault
    store = OpenBaoSecretStore(addr, "token")

    with pytest.raises(SecretStoreUnavailableError):
        store.get("conn-x")


def test_a_bundle_from_another_ca_is_refused(https_vault: tuple[str, Path], tmp_path: Path) -> None:
    addr, _ = https_vault
    local_ca.ensure(tmp_path / "other", tmp_path / "other-key")
    store = OpenBaoSecretStore(addr, "token", ca_bundle=str(tmp_path / "other" / "ca.pem"))

    with pytest.raises(SecretStoreUnavailableError):
        store.get("conn-x")


def test_a_bundle_that_names_no_file_fails_at_boot(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="OPENBAO_CA_BUNDLE"):
        Settings(openbao_ca_bundle=str(tmp_path / "missing.pem"), _env_file=None)
