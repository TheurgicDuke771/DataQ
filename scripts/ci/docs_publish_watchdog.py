"""Fail when the Docs workflow has silently stopped publishing.

A Pages deploy queued behind a job that never starts holds the `pages` concurrency group: every
later Docs run waits, is cancelled by the next one, and nothing fails. Two signals catch it:

- a Docs run still waiting (queued / pending / waiting / requested) past the grace period;
- a commit on main that touches a Docs path, older than the grace period, with no successful
  push or dispatch Docs run on main started after it.

Run on a schedule from `.github/workflows/docs-watchdog.yml`; reads only. Needs `gh` and git.
"""

from __future__ import annotations

import json
import subprocess  # nosec B404
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

WAITING = frozenset({"queued", "pending", "waiting", "requested"})
# A manual dispatch on main republishes `latest` (the documented remedy); a pull_request run never
# deploys.
PUBLISHING_EVENTS = frozenset({"push", "workflow_dispatch"})
GRACE = timedelta(hours=2)
WORKFLOW = Path(".github/workflows/docs.yml")


@dataclass(frozen=True)
class Run:
    id: int
    status: str
    conclusion: str | None
    event: str
    branch: str
    created_at: datetime
    url: str


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def run_from_api(item: dict[str, object]) -> Run:
    return Run(
        id=int(str(item["id"])),
        status=str(item["status"]),
        conclusion=None if item.get("conclusion") is None else str(item["conclusion"]),
        event=str(item["event"]),
        branch=str(item.get("head_branch") or ""),
        created_at=_parse_time(str(item["created_at"])),
        url=str(item["html_url"]),
    )


def stuck_runs(runs: list[Run], now: datetime, grace: timedelta = GRACE) -> list[Run]:
    return [r for r in runs if r.status in WAITING and now - r.created_at > grace]


def unpublished_since(
    commit_time: datetime, runs: list[Run], now: datetime, grace: timedelta = GRACE
) -> bool:
    """True when a Docs-path commit on main has gone unpublished past the grace period."""
    if now - commit_time <= grace:
        return False
    return not any(
        r.event in PUBLISHING_EVENTS
        and r.branch == "main"
        and r.conclusion == "success"
        and r.created_at >= commit_time
        for r in runs
    )


def docs_paths(workflow: Path = WORKFLOW) -> list[str]:
    """The push path filters of the Docs workflow, as git pathspecs."""
    data = yaml.safe_load(workflow.read_text())
    on = data.get("on", data.get(True))  # YAML 1.1 reads a bare `on` key as True
    return [p.removesuffix("/**") for p in on["push"]["paths"]]


def _out(*argv: str) -> str:
    # Fixed argv from this module only, no shell.
    return subprocess.run(  # noqa: S603  # nosec B603
        argv, check=True, capture_output=True, text=True
    ).stdout


def main() -> int:
    now = datetime.now(UTC)
    log = _out("git", "log", "-1", "--format=%H %cI", "origin/main", "--", *docs_paths()).split()
    raw = json.loads(
        _out("gh", "api", "repos/{owner}/{repo}/actions/workflows/docs.yml/runs?per_page=100")
    )
    runs = [run_from_api(item) for item in raw["workflow_runs"]]

    failed = False
    for run in stuck_runs(runs, now):
        print(
            f"::error::Docs run {run.id} has been {run.status} since "
            f"{run.created_at:%Y-%m-%d %H:%M}Z and holds the pages queue — cancel it: {run.url}"
        )
        failed = True
    if log:
        sha, committed = log[0], _parse_time(log[1])
        if unpublished_since(committed, runs, now):
            print(
                f"::error::main commit {sha[:12]} ({committed:%Y-%m-%d %H:%M}Z) touches the docs "
                "and no Docs run on main has published since — check the Docs queue"
            )
            failed = True
    if not failed:
        print("docs publishing is current")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
