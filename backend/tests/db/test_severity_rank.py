"""Drift guards for the single severity-rank source (#655).

`db.models.SEVERITY_RANK` is the one canonical "which run outcome is worse"
ordering — alert dedup, the RunReport builder, run-outcome rollups and the
alerting `FAILING_TIERS` set all derive from it rather than keeping independent
copies. These tests pin that single source so a new/reordered tier can't
silently diverge them.
"""

from __future__ import annotations

from backend.app.alerting.base import FAILING_TIERS
from backend.app.db.models import _RESULT_SEVERITY_TIERS, SEVERITY_RANK


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
    # The alerting failing-tier set is derived from the rank map, not a 2nd copy.
    assert FAILING_TIERS == tuple(SEVERITY_RANK)
    assert "pass" not in FAILING_TIERS
    assert "skip" not in SEVERITY_RANK and "error" not in SEVERITY_RANK
