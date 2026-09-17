"""celery-beat runs as its own service, never embedded in the worker (#1811).

A worker OOM (overlapping large suites under the concurrency=4 / 2 GiB rig, #1790) must never be
able to take the scheduler down with it (the #405 class). That only holds if (a) no worker launch
command anywhere carries `-B`, (b) every deploy topology declares a standalone beat process, and
(c) beat is never scaled beyond one instance — two would double-fire every periodic task.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]

_COMPOSE_FILES = ("docker-compose.yml", "docker-compose.ghcr.yml")
_WORKER_LAUNCH_FILES = (
    *_COMPOSE_FILES,
    "deploy/terraform/azure/containerapps.tf",
    "deploy/terraform/aws/ecs.tf",
)


def _worker_launch_commands(text: str) -> list[str]:
    pattern = re.compile(r"^.*backend\.app\.worker\.celery_app.*\bworker\b.*$", re.MULTILINE)
    return [re.sub(r'["\[\]]', " ", m.group(0)) for m in pattern.finditer(text)]


def _service_block(compose_text: str, service: str) -> str | None:
    """No PyYAML dependency (not a direct dep — CLAUDE.md single-source-of-truth rule): a
    top-level-key regex over the ``services:`` block is exactly `test_worker_launch_commands.py`'s
    existing approach for this same file, applied to service boundaries instead of command lines.
    """
    match = re.search(rf"^  {re.escape(service)}:\n((?:    .*\n|\n)*)", compose_text, re.MULTILINE)
    return match.group(1) if match else None


def test_no_worker_launch_command_carries_dash_b() -> None:
    """`-B` runs beat embedded in the worker process — the exact topology #1811 removed. A
    stray `-B` anywhere means a future OOM can take the scheduler down again.
    """
    for path in _WORKER_LAUNCH_FILES:
        text = (_ROOT / path).read_text()
        commands = _worker_launch_commands(text)
        assert commands, f"{path}: no worker launch command found"
        for command in commands:
            tokens = command.split()
            assert "-B" not in tokens, f"{path}: worker launch still carries -B: {command.strip()}"


def test_compose_files_declare_a_standalone_beat_service() -> None:
    """Each compose file must run beat as its OWN service — not folded into the
    worker's command — with the same `celery ... beat` invocation the deploy
    manifests use.
    """
    for path in _COMPOSE_FILES:
        text = (_ROOT / path).read_text()
        block = _service_block(text, "beat")
        assert block is not None, f"{path}: no 'beat' service"
        command_match = re.search(r"command:\s*(.+)", block)
        assert command_match, f"{path}: beat service has no command"
        command = command_match.group(1)
        assert "backend.app.worker.celery_app" in command
        assert re.search(r"\bbeat\b", command), f"{path}: beat service doesn't run `celery beat`"
        assert "-B" not in command.split()
        # docker compose runs exactly one container per service unless `deploy.replicas` overrides
        # it — the beat block must carry no such override.
        assert "replicas:" not in block, f"{path}: beat must run as ONE replica, no deploy.replicas"


def test_azure_beat_container_app_is_pinned_to_one_replica() -> None:
    """min_replicas == max_replicas == 1: beat must run exactly once, or every
    periodic task (orchestration polling, scheduled dispatch, sweeps) fires twice.
    """
    tf = (_ROOT / "deploy/terraform/azure/containerapps.tf").read_text()
    match = re.search(r'resource "azurerm_container_app" "beat" \{(.*?)\n\}\n', tf, re.DOTALL)
    assert match, "no azurerm_container_app.beat resource found"
    body = match.group(1)
    assert re.search(r"min_replicas\s*=\s*1", body)
    assert re.search(r"max_replicas\s*=\s*1", body)
    assert "-B" not in body
    assert re.search(r'"beat"', body)


def test_aws_beat_service_is_pinned_to_one_task() -> None:
    """desired_count == 1, and the rolling-deploy config stops the old task before
    starting a new one (never two beats running at once mid-roll).
    """
    tf = (_ROOT / "deploy/terraform/aws/ecs.tf").read_text()
    match = re.search(r'resource "aws_ecs_service" "beat" \{(.*?)\n\}\n', tf, re.DOTALL)
    assert match, "no aws_ecs_service.beat resource found"
    body = match.group(1)
    assert re.search(r"desired_count\s*=\s*1", body)
    assert re.search(r"deployment_minimum_healthy_percent\s*=\s*0", body)
    assert re.search(r"deployment_maximum_percent\s*=\s*100", body)
