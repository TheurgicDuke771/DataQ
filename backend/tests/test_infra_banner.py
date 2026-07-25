"""The degraded-run banner in conftest (#977).

These guard a piece of *diagnostic* code, which is exactly the kind that rots
unnoticed: nothing else fails when it goes wrong, so the only signal is a
person mistaking a third-coverage run for a green one — the failure this
banner exists to prevent.

Two of the three defects these lock down survived a manual four-state
walkthrough of the feature. `trylast` was missed because every hand-check
passed `--no-cov`, so the coverage table that buries the banner was never
rendered; the psycopg-only `connect_args` was missed because every hand-check
used a postgres URL. Both are cases the manual test could not express.
"""

from __future__ import annotations

from typing import Any

import backend.tests.conftest as conftest


class _FakeReporter:
    """Stands in for pytest's TerminalReporter, capturing written lines."""

    def __init__(self, **stats: list[object]) -> None:
        self.stats: dict[str, list[object]] = stats
        self.lines: list[str] = []

    def write_line(self, line: str, **_: Any) -> None:
        self.lines.append(line)


def _banner(monkeypatch: Any, status: list[tuple[str, str | None]], **stats: list[object]) -> str:
    monkeypatch.setattr(conftest, "_INFRA_STATUS_CACHE", status)
    reporter = _FakeReporter(**stats)
    conftest.pytest_terminal_summary(reporter)
    return "\n".join(reporter.lines)


# --- the banner fires only when it should ----------------------------------


def test_healthy_infra_prints_nothing(monkeypatch: Any) -> None:
    """A quiet gate is the whole point — noise on a healthy run trains people
    to ignore it, which is how the banner would stop working."""
    out = _banner(monkeypatch, [("postgres (test DB)", None), ("secret store (env)", None)])
    assert out == ""


def test_degraded_infra_names_the_service_and_the_fix(monkeypatch: Any) -> None:
    out = _banner(
        monkeypatch,
        [("postgres (test DB)", "not reachable"), ("secret store (redis)", None)],
        skipped=[object()],
    )
    assert "DEGRADED RUN" in out
    assert "postgres (test DB) — not reachable" in out
    assert "docker compose up -d postgres redis" in out
    # The healthy service must not be listed as unavailable.
    assert "secret store (redis) —" not in out


# --- the banner claims only what happened ----------------------------------
#
# A missing Postgres makes tests SKIP; a missing secret store makes them FAIL.
# Reporting one as the other would be the same untrue-status defect the banner
# is meant to catch.


def test_skips_are_reported_as_skips_not_failures(monkeypatch: Any) -> None:
    out = _banner(
        monkeypatch,
        [("postgres (test DB)", "not reachable")],
        skipped=[object(), object()],
    )
    assert "2 test(s) skipped" in out
    assert "failed/errored" not in out


def test_failures_are_reported_as_failures_not_skips(monkeypatch: Any) -> None:
    """Regression: an early draft printed "0 test(s) skipped" on this path."""
    out = _banner(
        monkeypatch,
        [("secret store (redis)", "not reachable")],
        failed=[object()],
        error=[object()],
    )
    assert "2 test(s) failed/errored" in out
    assert "skipped" not in out


def test_neither_skips_nor_failures_still_warns_without_inventing_counts(
    monkeypatch: Any,
) -> None:
    out = _banner(monkeypatch, [("secret store (redis)", "not reachable")])
    assert "DEGRADED RUN" in out
    assert "test(s) skipped" not in out
    assert "test(s) failed" not in out


# --- ordering: the banner must survive the coverage table ------------------


def test_terminal_summary_is_trylast() -> None:
    """Without ``trylast``, this hook runs BEFORE pytest-cov's summary and the
    banner prints above a ~140-line term-missing table — scrolling off exactly
    like the header it backs up. Verified by hand: banner at line 6, real
    summary at line 149. This is a behavioural requirement, not a style choice.
    """
    opts = getattr(conftest.pytest_terminal_summary, "pytest_impl", {})
    assert opts.get("trylast") is True


# --- the probe must not cry wolf -------------------------------------------


def test_probe_does_not_claim_unreachable_for_a_non_psycopg_driver(monkeypatch: Any) -> None:
    """``connect_timeout`` is libpq-specific. Passing it to another driver raises
    at connect time; swallowing that into "not reachable" would report a healthy
    database as down.
    """
    monkeypatch.setattr(conftest, "TEST_DATABASE_URL", "sqlite://")  # in-memory, always up
    assert conftest._probe_postgres() is None


def test_probe_reports_a_genuinely_dead_postgres(monkeypatch: Any) -> None:
    """The other half: a real postgres URL pointing nowhere must still be caught,
    so the wolf-guard above cannot silence a true alarm.
    """
    monkeypatch.setattr(
        conftest,
        "TEST_DATABASE_URL",
        "postgresql+psycopg2://nobody:nobody@127.0.0.1:59999/nope",
    )
    assert conftest._probe_postgres() == "not reachable"


def test_probe_without_a_url_explains_itself(monkeypatch: Any) -> None:
    monkeypatch.setattr(conftest, "TEST_DATABASE_URL", None)
    reason = conftest._probe_postgres()
    assert reason is not None and "TEST_DATABASE_URL" in reason
