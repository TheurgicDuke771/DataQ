"""The worker launch command is declared in four places; they must agree (#1777)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_LAUNCH_FILES = (
    "docker-compose.yml",
    "docker-compose.ghcr.yml",
    "deploy/terraform/azure/containerapps.tf",
    "deploy/terraform/aws/ecs.tf",
)
_WORKER_LINE = re.compile(r"^.*backend\.app\.worker\.celery_app.*\bworker\b.*$", re.MULTILINE)


def _worker_commands(path: str) -> list[str]:
    text = (_ROOT / path).read_text()
    # Terraform spells the command as a JSON-ish array; normalise both shapes to one string.
    return [re.sub(r'["\[\]]', " ", m.group(0)) for m in _WORKER_LINE.finditer(text)]


@pytest.mark.parametrize("path", _LAUNCH_FILES)
def test_every_worker_launch_consumes_both_queues(path: str) -> None:
    """A worker that omits the `llm` queue silently never consumes it (#1777). Pool size is
    deliberately NOT asserted here: it lives in `celery_app.conf` (#1790) so it ships with the
    image rather than needing a coordinated IaC apply.
    """
    commands = _worker_commands(path)
    assert commands, f"{path}: no worker launch command found"
    for command in commands:
        assert "celery,llm" in command, f"{path}: {command.strip()}"
        assert "--concurrency" not in command, f"{path}: pool size belongs in celery_app.conf"
