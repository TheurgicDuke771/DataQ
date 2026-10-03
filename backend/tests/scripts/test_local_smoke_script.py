"""`scripts/local-smoke.sh` — what can be checked without a running stack (#2330)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "local-smoke.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _bash(script: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={**os.environ, **env},
    )


def _helpers() -> str:
    """The script's helper functions, without the checks that need a stack."""
    text = _SCRIPT.read_text()
    start, end = text.index("failures=0"), text.index('workdir="$(mktemp -d)"')
    return "GREEN=; RED=; NC=\n" + text[start:end]


def test_the_script_parses() -> None:
    assert subprocess.run(["bash", "-n", str(_SCRIPT)], check=False).returncode == 0


def test_no_address_to_sign_in_as_is_a_usage_error() -> None:
    result = _bash(f"bash {_SCRIPT}", DATAQ_SIGNIN_EMAIL="")
    assert result.returncode == 2
    assert "--email" in result.stderr


def test_a_check_whose_actual_value_split_in_two_fails() -> None:
    """Found live: an inline JSON body was brace-expanded into two requests, `check` received
    four arguments, compared the two results with each other, and reported a pass.
    """
    result = _bash(_helpers() + 'check "signs in" 422 422 200; echo "failures=${failures}"')
    assert "failures=1" in result.stdout
    assert "malformed" in result.stdout


def test_a_matching_check_passes_and_a_mismatch_counts() -> None:
    result = _bash(_helpers() + 'check a 200 200; check b 401 200; echo "failures=${failures}"')
    assert "failures=1" in result.stdout


def test_json_bodies_survive_a_comma_without_brace_expansion() -> None:
    result = _bash(
        _helpers() + """body="$(json '{"email":"%s","code":"%s"}' a@b.c 123456)"; echo "${body}" """
    )
    assert result.stdout.strip() == '{"email":"a@b.c","code":"123456"}'
