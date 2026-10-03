"""The prebuilt-image stack is production-like: one way in, prod settings (#2326).

`docker-compose.ghcr.yml` is what an evaluator runs, so it should carry the posture a real
deployment has (ADR 0028 §5): the frontend is the only published surface and the api is reached
through its proxy. A stray `ports:` entry on the api or a datastore would quietly undo that.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_COMPOSE = (_ROOT / "docker-compose.ghcr.yml").read_text()

# host-binding -> why it may be published.
_ALLOWED_PORTS = {
    "127.0.0.1:3000:8080": "the UI, the stack's only public surface",
    "127.0.0.1:8025:8025": "the local inbox the sign-in code is read from",
}


def _published_ports() -> dict[str, list[str]]:
    """Service name -> its published port bindings (regex, as the sibling compose tests do)."""
    services = _COMPOSE.split("\nservices:\n", 1)[1].split("\nvolumes:\n", 1)[0]
    found: dict[str, list[str]] = {}
    for block in re.finditer(r"^  ([\w-]+):\n((?:    .*\n|\n| *#.*\n)*)", services, re.MULTILINE):
        ports = re.search(r"^    ports:\n((?:      .*\n)+)", block.group(2), re.MULTILINE)
        if ports:
            found[block.group(1)] = re.findall(r'-\s*"([^"]+)"', ports.group(1))
    return found


def test_only_the_ui_and_the_inbox_are_published() -> None:
    published = _published_ports()
    assert published == {
        "frontend": ["127.0.0.1:3000:8080"],
        "mailpit": ["127.0.0.1:8025:8025"],
    }, f"only {sorted(_ALLOWED_PORTS)} may be published; found {published}"


def test_every_published_port_binds_loopback() -> None:
    for service, bindings in _published_ports().items():
        for binding in bindings:
            assert binding.startswith("127.0.0.1:"), f"{service} publishes {binding} off loopback"


def test_the_stack_runs_prod_settings_by_default() -> None:
    assert "ENVIRONMENT: ${DATAQ_ENVIRONMENT:-prod}" in _COMPOSE
    assert 'RATE_LIMIT_ENABLED: "true"' in _COMPOSE


def test_no_service_runs_a_reloader() -> None:
    assert "--reload" not in _COMPOSE


def test_every_ports_key_is_in_the_form_the_guard_reads() -> None:
    """`_published_ports` reads block-style lists only; an inline list would slip past it."""
    lines = re.findall(r"^[ \t]*ports:.*$", _COMPOSE, re.MULTILINE)
    assert lines == ["    ports:"] * len(_published_ports())


def _block(service: str) -> str:
    match = re.search(
        rf"^  {re.escape(service)}:\n((?:    .*\n|\n| *#.*\n)*)", _COMPOSE, re.MULTILINE
    )
    assert match, service
    return match.group(1)


def _takes_app_env(service: str) -> bool:
    """Whether the service runs the backend with the shared app env (whole, or merged)."""
    block = _block(service)
    return "environment: *app-env" in block or "<<: *app-env" in block


def test_the_ui_terminates_tls_and_sends_no_hsts() -> None:
    frontend = _block("frontend")
    assert "DATAQ_TLS_CERT: /tls/localhost.pem" in frontend
    assert "DATAQ_TLS_KEY: /tls/localhost.key" in frontend
    # HSTS binds the host `localhost`, not the port: it would force HTTPS on the inbox too.
    assert 'DATAQ_HSTS: ""' in frontend


def test_only_the_generator_mounts_the_ca_private_key() -> None:
    holders = [
        name
        for name in re.findall(
            r"^  ([\w-]+):\n", _COMPOSE.split("\nservices:\n", 1)[1], re.MULTILINE
        )
        if "ghcr_local_ca_key:" in _block(name)
    ]
    assert holders == ["local-ca"]


# ── TLS between the stack's own services (#2328) ─────────────────────────────


def test_the_app_verifies_its_datastores_against_the_local_ca() -> None:
    assert "sslmode=verify-full&sslrootcert=/certs/ca.pem" in _COMPOSE
    assert "rediss://redis:6379/0?ssl_cert_reqs=required&ssl_ca_certs=/certs/ca.pem" in _COMPOSE
    assert "redis://" not in _COMPOSE.replace("rediss://", "")


def test_redis_serves_tls_only() -> None:
    redis = _block("redis")
    assert "--port 0 --tls-port 6379" in redis
    assert '"--tls"' in redis  # the healthcheck must speak TLS too, or it reports a dead server


def test_sign_in_mail_goes_over_verified_starttls() -> None:
    assert (
        "AUTH_EMAIL_TLS_MODE: ${DATAQ_SIGNIN_EMAIL:+${AUTH_EMAIL_TLS_MODE:-starttls}}" in _COMPOSE
    )
    # `-`, not `:-`: an operator pointing at a public relay sets it EMPTY to use the usual CAs.
    assert (
        "AUTH_EMAIL_CA_BUNDLE: ${DATAQ_SIGNIN_EMAIL:+${AUTH_EMAIL_CA_BUNDLE-/certs/ca.pem}}"
        in _COMPOSE
    )
    mailpit = _block("mailpit")
    assert 'MP_SMTP_REQUIRE_STARTTLS: "true"' in mailpit
    assert "MP_SMTP_AUTH_ALLOW_INSECURE" not in mailpit


def test_each_private_key_volume_reaches_only_its_own_service() -> None:
    names = re.findall(r"^  ([\w-]+):\n", _COMPOSE.split("\nservices:\n", 1)[1], re.MULTILINE)
    for owner in ("frontend", "postgres", "redis", "mailpit", "openbao", "api"):
        holders = sorted(n for n in names if f"ghcr_tls_{owner}:" in _block(n))
        assert holders == sorted({"local-ca", owner}), f"{owner}'s key is mounted by {holders}"


def test_every_backend_container_mounts_the_ca_it_is_told_to_verify_with() -> None:
    names = re.findall(r"^  ([\w-]+):\n", _COMPOSE.split("\nservices:\n", 1)[1], re.MULTILINE)
    backend = [n for n in names if _takes_app_env(n)]
    assert len(backend) == 6
    for name in backend:
        block = _block(name)
        mounts_ca = "volumes: *app-volumes" in block or "- ghcr_local_ca:/certs:ro" in block
        assert mounts_ca, name


# ── Data on disk (#2342) ─────────────────────────────────────────────────────

_DATA_DIR = "${DATAQ_DATA_DIR:-./dataq-data}"


def test_the_databases_and_the_vault_keep_their_data_in_the_data_directory() -> None:
    assert f"- {_DATA_DIR}/postgres:/var/lib/postgresql/data" in _block("postgres")
    assert f"- {_DATA_DIR}/demo-warehouse:/var/lib/postgresql/data" in _block("demo-warehouse")
    assert f"- {_DATA_DIR}/openbao:/openbao/file" in _block("openbao")


def test_the_vault_is_not_in_dev_mode() -> None:
    """Dev mode is in-memory: every stored credential would be lost on restart."""
    assert "BAO_DEV_" not in _COMPOSE
    openbao = _block("openbao")
    assert '"raft"' in openbao
    assert '"static"' in openbao


def test_app_containers_wait_for_a_usable_vault_not_just_a_listening_one() -> None:
    names = re.findall(r"^  ([\w-]+):\n", _COMPOSE.split("\nservices:\n", 1)[1], re.MULTILINE)
    for name in (n for n in names if _takes_app_env(n)):
        block = _block(name)
        assert "vault-init:\n        condition: service_completed_successfully" in block, name


# ── Real mailboxes (#2336) ───────────────────────────────────────────────────


def test_a_relay_password_reaches_only_the_one_shot_that_stores_it() -> None:
    names = re.findall(r"^  ([\w-]+):\n", _COMPOSE.split("\nservices:\n", 1)[1], re.MULTILINE)
    for variable in ("DATAQ_SMTP_PASSWORD", "DATAQ_ALERT_SMTP_PASSWORD"):
        holders = [n for n in names if f"{variable}:" in _block(n)]
        assert holders == ["otp-mail-secret"], f"{variable} is passed to {holders}"
    shared_env = _COMPOSE.split("x-app-env: &app-env\n", 1)[1].split("\nservices:\n", 1)[0]
    assert "SMTP_PASSWORD" not in shared_env


def test_the_alert_mailer_is_off_until_a_username_is_given() -> None:
    secret_name = "${EMAIL_USERNAME:+${EMAIL_PASSWORD_SECRET_NAME:-dataq-alert-smtp}}"
    assert f"EMAIL_PASSWORD_SECRET_NAME: {secret_name}" in _COMPOSE
    assert "EMAIL_TO: ${EMAIL_TO:-}" in _COMPOSE


# ── No plaintext hop left inside the stack (#2338) ───────────────────────────


def test_the_vault_is_reached_over_https_verified_against_the_local_ca() -> None:
    assert _COMPOSE.count('OPENBAO_ADDR: "https://openbao:8200"') == 2  # the app env and vault-init
    assert _COMPOSE.count("OPENBAO_CA_BUNDLE: /certs/ca.pem") == 2
    openbao = _block("openbao")
    assert '"tls_cert_file": "/tls/cert.pem"' in openbao
    assert "tls_disable" not in openbao
    assert "http://openbao" not in _COMPOSE


def test_the_proxy_verifies_the_api_and_the_api_serves_tls() -> None:
    frontend = _block("frontend")
    assert "DATAQ_API_UPSTREAM: https://api:8000" in frontend
    assert "DATAQ_API_UPSTREAM_CA: /certs/ca.pem" in frontend
    assert "--ssl-certfile /tls/cert.pem --ssl-keyfile /tls/key.pem" in _block("api")


def test_postgres_admits_network_clients_over_tls_only() -> None:
    postgres = _block("postgres")
    assert "hostssl all all all trust" in postgres
    # A plain `host` line would admit a client that skipped TLS.
    assert not re.search(r"\\nhost\s", postgres)
    assert "hba_file=/tmp/pg_hba.conf" in postgres
