"""Bring the local stack's vault up with its data on disk, with no operator step.

`docker-compose.ghcr.yml` runs OpenBao on integrated storage in the stack's data directory,
auto-unsealed by a key file beside it. Two one-shots run this module:

    python -m backend.scripts.local_vault prepare /vault 100:1000   # before the server: the
                                                    # seal key, owned by the server's user
    python -m backend.scripts.local_vault init /vault      # after it: initialize, mount, token

`init` is idempotent. It initializes a fresh vault, mounts the KV v2 engine the app uses, and
makes `OPENBAO_TOKEN` a token that can read and write that mount and nothing else, so the app
keeps authenticating the way it always has while the secrets now survive a restart.

This is a convenience for a stack on your own machine, not a production vault: the seal key
and the root token sit in the same directory as the data, so whoever can read that directory
can read every stored credential.
"""

from __future__ import annotations

import json
import os
import secrets
import ssl
import sys
import time
from pathlib import Path
from typing import Any

import httpx

SEAL_KEY = "seal.key"
ROOT_FILE = "root.json"
POLICY = "dataq-app"
# Local tokens are meant to outlive a long-running stack; the root-held file re-mints on demand.
APP_LEASE = "87600h"


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


def _save(path: Path, state: dict[str, str]) -> None:
    _write_private(path, json.dumps(state).encode())


def prepare(directory: Path, owner: tuple[int, int] | None = None) -> str:
    """Create the auto-unseal key once, and the directory the server stores its data in (it
    does not create that itself). Losing the key makes the stored data unreadable.
    """
    directory.mkdir(parents=True, exist_ok=True)
    key, data = directory / SEAL_KEY, directory / "raft"
    has_key = key.is_file() and key.stat().st_size == 32
    if not has_key and data.is_dir() and any(data.iterdir()):
        raise SystemExit(
            f"{directory} holds vault data but no usable {SEAL_KEY}: that data cannot be unsealed. "
            "Restore the key, or remove the directory to start an empty vault."
        )
    data.mkdir(exist_ok=True)
    action = "kept"
    if not has_key:
        _write_private(key, secrets.token_bytes(32))
        action = "created"
    # Every run, and explicitly: the server reads the key as its own unprivileged user, and
    # its image only takes ownership of the directory when the directory itself is foreign.
    if owner is not None and os.geteuid() == 0:
        for path in (directory, data, key):
            os.chown(path, *owner)
    return action


def _policy(mount: str) -> str:
    return (
        f'path "{mount}/data/*" {{ capabilities = ["create", "read", "update"] }}\n'
        f'path "{mount}/metadata/*" {{ capabilities = ["read", "list", "delete"] }}\n'
        f'path "{mount}/metadata" {{ capabilities = ["list"] }}\n'
    )


class _Vault:
    def __init__(
        self, addr: str, client: httpx.Client | None = None, ca_bundle: str | None = None
    ) -> None:
        verify: ssl.SSLContext | bool = True
        if ca_bundle:
            verify = ssl.create_default_context(cafile=ca_bundle)
        self._client = client or httpx.Client(base_url=addr, timeout=10.0, verify=verify)
        self.root: str | None = None

    def call(
        self,
        method: str,
        path: str,
        *,
        ok: tuple[int, ...] = (200, 204),
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {"X-Vault-Token": self.root} if self.root else {}
        response = self._client.request(method, path, headers=headers, json=json)
        if response.status_code not in ok:
            raise SystemExit(f"vault {method} {path} answered {response.status_code}")
        body: dict[str, Any] = response.json() if response.content else {}
        return body

    def wait_until(self, *, sealed: bool, attempts: int = 60) -> None:
        for _ in range(attempts):
            try:
                status = self.call("GET", "/v1/sys/seal-status")
                if status.get("initialized") and status.get("sealed") is sealed:
                    return
            except (httpx.HTTPError, SystemExit):
                # Not up yet, or answering an error while it starts: that is what this
                # loop waits out. Running out of attempts is reported below.
                pass
            time.sleep(1)
        raise SystemExit(
            "the vault did not unseal itself; is the seal key the one it was created with?"
        )


def init(
    directory: Path, *, addr: str, mount: str, app_token: str, vault: _Vault | None = None
) -> dict[str, str]:
    """Make the running vault usable by the app. Returns what was done."""
    vault = vault or _Vault(addr, ca_bundle=os.environ.get("OPENBAO_CA_BUNDLE") or None)
    root_file = directory / ROOT_FILE
    done = {"vault": "kept", "mount": "kept", "access": "kept"}

    if not vault.call("GET", "/v1/sys/init")["initialized"]:
        created = vault.call(
            "PUT", "/v1/sys/init", json={"recovery_shares": 1, "recovery_threshold": 1}
        )
        _save(root_file, {"root_token": created["root_token"]})
        done["vault"] = "initialized"
    if not root_file.is_file():
        raise SystemExit(
            f"the vault is initialized but {root_file} is missing, so its app token cannot be "
            "managed. Restore the file, or remove the directory to start an empty vault."
        )
    vault.wait_until(sealed=False)
    state = json.loads(root_file.read_text())
    vault.root = state["root_token"]

    if f"{mount}/" not in vault.call("GET", "/v1/sys/mounts"):
        vault.call(
            "POST", f"/v1/sys/mounts/{mount}", json={"type": "kv", "options": {"version": "2"}}
        )
        done["mount"] = "created"
    vault.call("PUT", f"/v1/sys/policies/acl/{POLICY}", json={"policy": _policy(mount)})
    vault.call("POST", "/v1/sys/auth/token/tune", json={"max_lease_ttl": APP_LEASE})

    known = vault._client.post(
        "/v1/auth/token/lookup", headers={"X-Vault-Token": vault.root}, json={"token": app_token}
    )
    if known.status_code != 200:
        # A new OPENBAO_TOKEN replaces the previous one rather than joining it.
        if state.get("app_accessor"):
            vault.call(
                "POST",
                "/v1/auth/token/revoke-accessor",
                json={"accessor": state["app_accessor"]},
                ok=(200, 204, 400, 403, 404),
            )
        minted = vault.call(
            "POST",
            "/v1/auth/token/create-orphan",
            json={
                "id": app_token,
                "policies": [POLICY],
                "ttl": APP_LEASE,
                "no_default_policy": True,
            },
        )
        _save(root_file, {**state, "app_accessor": minted["auth"]["accessor"]})
        done["access"] = "created"
    return done


def main(argv: list[str]) -> None:
    usage = "usage: python -m backend.scripts.local_vault prepare <dir> [uid:gid] | init <dir>"
    if len(argv) < 3 or argv[1] not in ("prepare", "init"):
        raise SystemExit(usage)
    directory = Path(argv[2])
    if argv[1] == "prepare":
        owner = None
        if len(argv) == 4:
            uid, gid = argv[3].split(":")
            owner = (int(uid), int(gid))
        print(f"Local vault: seal key {prepare(directory, owner)}")
        return
    token = os.environ.get("OPENBAO_TOKEN", "").strip()
    if not token:
        raise SystemExit("OPENBAO_TOKEN is not set")
    done = init(
        directory,
        addr=os.environ.get("OPENBAO_ADDR", "http://openbao:8200"),
        mount=os.environ.get("OPENBAO_MOUNT", "secret"),
        app_token=token,
    )
    print("Local vault: " + " ".join(f"{k}={v}" for k, v in done.items()))


if __name__ == "__main__":
    main(sys.argv)
