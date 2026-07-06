"""Drift guards for the single severity-rank source (#655).

`db.models.SEVERITY_RANK` is the one canonical "which run outcome is worse"
ordering — alert dedup, the RunReport builder, run-outcome rollups and the
alerting `FAILING_TIERS` set all derive from it rather than keeping independent
copies. These tests pin that single source so a new/reordered tier can't
silently diverge them.
"""

from __future__ import annotations

from backend.app.db.models import (
    _RESULT_SEVERITY_TIERS,
    FAILING_TIERS,
    SEVERITY_RANK,
    worst_severity,
)


def test_severity_rank_values() -> None:
    # The failing tiers, ranked worst-last, excluding `pass` and the operational
    # statuses (skip/error never rank — ADR 0005).
    assert SEVERITY_RANK == {"warn": 1, "fail": 2, "critical": 3}


def test_severity_rank_derives_from_the_tier_vocabulary() -> None:
    # Order + membership come from `_RESULT_SEVERITY_TIERS` (minus `pass`), so
    # editing that one tuple is the only way to change the ranking.
    expected = tuple(t for t in _RESULT_SEVERITY_TIERS if t != "pass")
    assert tuple(SEVERITY_RANK) == expected
    assert list(SEVERITY_RANK.values()) == sorted(SEVERITY_RANK.values())  # worst last


def test_failing_tiers_is_the_same_source() -> None:
    # The failing-tier set is derived from the rank map, not a 2nd hardcoded copy.
    assert FAILING_TIERS == tuple(SEVERITY_RANK)
    assert "pass" not in FAILING_TIERS
    assert "skip" not in SEVERITY_RANK and "error" not in SEVERITY_RANK


def test_worst_severity_picks_the_highest_failing_tier() -> None:
    # The shared helper both consumers now use: highest failing tier present, or
    # None when nothing breached; pass/skip/error never rank.
    assert worst_severity(["pass", "warn", "critical", "fail"]) == "critical"
    assert worst_severity(["pass", "warn"]) == "warn"
    assert worst_severity(["pass", "skip", "error"]) is None
    assert worst_severity([]) is None
    # Accepts any iterable of statuses (e.g. a status→count dict's keys).
    assert worst_severity({"pass": 3, "fail": 1}) == "fail"


def test_alerting_base_reexports_the_same_object() -> None:
    # The alerting layer imports FAILING_TIERS from its own base module (an explicit
    # re-export of the db.models source); prove it's the same object so a consumer
    # can't pick up a stale copy.
    from backend.app.alerting.base import FAILING_TIERS as BASE_FAILING_TIERS

    assert BASE_FAILING_TIERS is FAILING_TIERS
