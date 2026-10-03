"""The local stack's persistent vault bootstrap (#2342)."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

import httpx
import pytest

from backend.scripts import local_vault

# ── prepare: the seal key ────────────────────────────────────────────────────


def test_prepare_creates_a_private_key_and_the_data_directory(tmp_path: Path) -> None:
    assert local_vault.prepare(tmp_path) == "created"

    key = tmp_path / local_vault.SEAL_KEY
    assert key.stat().st_size == 32
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    # The server does not create its own storage directory; without it the container crash-loops.
    assert (tmp_path / "raft").is_dir()


def test_prepare_never_replaces_an_existing_key(tmp_path: Path) -> None:
    local_vault.prepare(tmp_path)
    before = (tmp_path / local_vault.SEAL_KEY).read_bytes()

    assert local_vault.prepare(tmp_path) == "kept"
    assert (tmp_path / local_vault.SEAL_KEY).read_bytes() == before


def test_prepare_refuses_to_mint_a_key_over_existing_data(tmp_path: Path) -> None:
    """A fresh key beside old data would start a vault that can never unseal that data."""
    (tmp_path / "raft").mkdir()
    (tmp_path / "raft" / "vault.db").write_bytes(b"x")

    with pytest.raises(SystemExit, match="cannot be unsealed"):
        local_vault.prepare(tmp_path)
    assert not (tmp_path / local_vault.SEAL_KEY).exists()


# ── init: against a fake vault ───────────────────────────────────────────────


class _FakeVault:
    """The handful of endpoints `init` touches, with just enough state to be honest."""

    def __init__(self, *, initialized: bool = False) -> None:
        self.initialized = initialized
        self.mounts: dict[str, Any] = {"sys/": {}}
        self.policies: dict[str, str] = {}
        self.tokens: dict[str, dict[str, Any]] = {}
        self.revoked: list[str] = []
        self.calls: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.calls.append((method, path))
        body = json.loads(request.content) if request.content else {}
        root = request.headers.get("X-Vault-Token") == "root-token"
        if path == "/v1/sys/init" and method == "GET":
            return httpx.Response(200, json={"initialized": self.initialized})
        if path == "/v1/sys/init":
            self.initialized = True
            return httpx.Response(200, json={"root_token": "root-token"})
        if path == "/v1/sys/seal-status":
            return httpx.Response(200, json={"initialized": self.initialized, "sealed": False})
        if not root:
            return httpx.Response(403, json={"errors": ["permission denied"]})
        if path == "/v1/sys/mounts" and method == "GET":
            return httpx.Response(200, json=self.mounts)
        if path.startswith("/v1/sys/mounts/"):
            self.mounts[path.removeprefix("/v1/sys/mounts/") + "/"] = body
            return httpx.Response(204)
        if path.startswith("/v1/sys/policies/acl/"):
            self.policies[path.rsplit("/", 1)[1]] = body["policy"]
            return httpx.Response(204)
        if path == "/v1/sys/auth/token/tune":
            return httpx.Response(204)
        if path == "/v1/auth/token/lookup":
            known = body["token"] in self.tokens
            return httpx.Response(200 if known else 403, json={})
        if path == "/v1/auth/token/create-orphan":
            accessor = f"acc-{len(self.tokens) + 1}"
            self.tokens[body["id"]] = {**body, "accessor": accessor}
            return httpx.Response(200, json={"auth": {"accessor": accessor}})
        if path == "/v1/auth/token/revoke-accessor":
            self.revoked.append(body["accessor"])
            self.tokens = {
                k: v for k, v in self.tokens.items() if v["accessor"] != body["accessor"]
            }
            return httpx.Response(204)
        return httpx.Response(404)


def _init(tmp_path: Path, fake: _FakeVault, app_token: str = "app-1") -> dict[str, str]:
    client = httpx.Client(base_url="http://vault", transport=httpx.MockTransport(fake))
    return local_vault.init(
        tmp_path,
        addr="http://vault",
        mount="secret",
        app_token=app_token,
        vault=local_vault._Vault("http://vault", client),
    )


def test_a_fresh_vault_is_initialized_mounted_and_given_a_scoped_token(tmp_path: Path) -> None:
    fake = _FakeVault()

    assert _init(tmp_path, fake) == {
        "vault": "initialized",
        "mount": "created",
        "access": "created",
    }

    root_file = tmp_path / local_vault.ROOT_FILE
    assert stat.S_IMODE(root_file.stat().st_mode) == 0o600
    assert fake.mounts["secret/"] == {"type": "kv", "options": {"version": "2"}}
    token = fake.tokens["app-1"]
    # The app's token is bound to the one policy, without the default policy and never root.
    assert token["policies"] == [local_vault.POLICY]
    assert token["no_default_policy"] is True
    assert "root" not in token["policies"]


def test_the_policy_covers_the_apps_mount_and_nothing_else(tmp_path: Path) -> None:
    fake = _FakeVault()
    _init(tmp_path, fake)

    paths = [line.split('"')[1] for line in fake.policies[local_vault.POLICY].splitlines() if line]
    assert paths == ["secret/data/*", "secret/metadata/*", "secret/metadata"]
    assert "sudo" not in fake.policies[local_vault.POLICY]


def test_a_second_run_changes_nothing(tmp_path: Path) -> None:
    fake = _FakeVault()
    _init(tmp_path, fake)

    assert _init(tmp_path, fake) == {"vault": "kept", "mount": "kept", "access": "kept"}
    assert list(fake.tokens) == ["app-1"]
    assert fake.revoked == []


def test_a_new_app_token_replaces_the_previous_one(tmp_path: Path) -> None:
    """`OPENBAO_TOKEN` may differ on every start; old ones must not pile up as valid."""
    fake = _FakeVault()
    _init(tmp_path, fake, "app-1")

    assert _init(tmp_path, fake, "app-2")["access"] == "created"

    assert list(fake.tokens) == ["app-2"]
    assert fake.revoked == ["acc-1"]


def test_an_initialized_vault_without_its_root_file_is_refused(tmp_path: Path) -> None:
    """Re-initializing is impossible and guessing is worse: say what is missing."""
    fake = _FakeVault(initialized=True)

    with pytest.raises(SystemExit, match=r"root\.json is missing"):
        _init(tmp_path, fake)
    assert ("PUT", "/v1/sys/init") not in fake.calls
