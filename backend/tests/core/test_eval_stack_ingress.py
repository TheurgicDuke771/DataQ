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
