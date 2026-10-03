"""`scripts/local-ca.sh` — the parts that run without root or Docker (#2329)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from backend.scripts import local_ca

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "local-ca.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("openssl") is None, reason="needs bash + openssl"
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(_SCRIPT), *args], capture_output=True, text=True, timeout=60, check=False
    )


def test_the_script_parses() -> None:
    assert subprocess.run(["bash", "-n", str(_SCRIPT)], check=False).returncode == 0


def test_help_names_the_three_commands() -> None:
    result = _run("--help")
    assert result.returncode == 0
    for word in ("install", "uninstall", "status"):
        assert word in result.stdout


def test_an_unknown_argument_is_a_usage_error() -> None:
    assert _run("trust-everything").returncode == 2


def test_a_certificate_that_is_not_the_stacks_ca_is_never_trusted(tmp_path: Path) -> None:
    """The refusal comes before any trust store is touched, so this needs no root."""
    foreign = tmp_path / "foreign.pem"
    subprocess.run(
        [
            *("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1"),
            *("-subj", "/CN=Some Other CA"),
            *("-keyout", str(tmp_path / "k.pem"), "-out", str(foreign)),
        ],
        check=True,
        capture_output=True,
    )
    result = _run("install", "--ca", str(foreign))
    assert result.returncode == 0  # never fatal: the stack works with a browser warning
    assert "refusing to trust it" in result.stderr
    assert "Trusted in" not in result.stdout


def test_the_name_the_script_looks_for_is_the_name_the_generator_writes() -> None:
    assert f'CA_NAME="{local_ca.CA_NAME}"' in _SCRIPT.read_text()
