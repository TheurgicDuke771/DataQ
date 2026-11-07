"""`scripts/ci/docs_publish_watchdog.py` — the signals that a Docs publish silently stopped."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "ci" / "docs_publish_watchdog.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("dataq_docs_watchdog_under_test", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


wd = _load()
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)


def _run(
    hours_ago: float,
    status: str = "completed",
    conclusion: str | None = "success",
    event: str = "push",
    branch: str = "main",
) -> object:
    return wd.Run(
        id=1,
        status=status,
        conclusion=conclusion,
        event=event,
        branch=branch,
        created_at=NOW - timedelta(hours=hours_ago),
        url="https://example.invalid/run/1",
    )


def test_a_run_queued_past_the_grace_period_is_stuck() -> None:
    stuck = _run(3, status="queued", conclusion=None)
    fresh = _run(1, status="waiting", conclusion=None)
    running = _run(5, status="in_progress", conclusion=None)
    assert wd.stuck_runs([stuck, fresh, running], NOW) == [stuck]


def test_a_docs_commit_with_no_later_successful_main_run_is_unpublished() -> None:
    committed = NOW - timedelta(hours=4)
    earlier_success = _run(5)
    cancelled_after = _run(3, conclusion="cancelled")
    pr_success_after = _run(3, event="pull_request", branch="main")  # a fork's `main`
    tag_success_after = _run(3, branch="v1.2.0")  # a tag push publishes its version, not latest
    runs = [earlier_success, cancelled_after, pr_success_after, tag_success_after]
    assert wd.unpublished_since(committed, runs, NOW) is True


def test_a_successful_main_run_after_the_commit_means_published() -> None:
    committed = NOW - timedelta(hours=4)
    assert wd.unpublished_since(committed, [_run(3.9)], NOW) is False


def test_a_manual_dispatch_on_main_after_the_commit_means_published() -> None:
    committed = NOW - timedelta(hours=4)
    assert wd.unpublished_since(committed, [_run(1, event="workflow_dispatch")], NOW) is False


def test_a_commit_inside_the_grace_period_is_not_flagged() -> None:
    assert wd.unpublished_since(NOW - timedelta(minutes=30), [], NOW) is False


def test_docs_paths_are_the_docs_workflow_push_filters() -> None:
    paths = wd.docs_paths(Path(__file__).resolve().parents[3] / ".github/workflows/docs.yml")
    assert "docs/site" in paths and "mkdocs.yml" in paths
    assert not any(p.endswith("/**") for p in paths)


def test_run_from_api_reads_the_fields_the_checks_use() -> None:
    run = wd.run_from_api(
        {
            "id": 42,
            "status": "queued",
            "conclusion": None,
            "event": "push",
            "head_branch": "main",
            "created_at": "2026-11-07T09:00:00Z",
            "html_url": "https://example.invalid/run/42",
        }
    )
    assert (run.id, run.status, run.conclusion, run.branch) == (42, "queued", None, "main")
    assert run.created_at == datetime(2026, 11, 7, 9, 0, tzinfo=UTC)
