"""Unit tests for the live-probe disclosure seam (#1419, #1479).

The seam decides two things the four probe routes must not decide for
themselves: **whether values mask** (by destination, not by column) and **what
the audit event says**. These tests pin the decision table directly, because the
route-level tests can only observe the composite and would not distinguish
"masked for the right reason" from "masked by accident".

`mask_profile_columns` is tested for what it KEEPS as hard as for what it
removes. The whole premise of the destination rule is that no capability is lost
to a policy artifact, and a masker that quietly dropped `null_fraction` would
break that promise while every "is it masked?" assertion stayed green.
"""

from __future__ import annotations

import pytest

from backend.app.services.live_probe import (
    Destination,
    mask_profile_columns,
    sensitive_profile_columns,
    values_are_masked,
)
from backend.app.services.profile_service import ColumnProfile
from backend.app.services.run_service import _REDACTED_VALUE


def _col(
    name: str, *, top: list[str] | None = None, lo: object = "a", hi: object = "z"
) -> ColumnProfile:
    return ColumnProfile(
        column=name,
        null_count=3,
        null_fraction=0.25,
        distinct_count=7,
        min_value=lo,
        max_value=hi,
        top_values=[{"value": v, "count": 2} for v in (top or ["x", "y"])],
    )


# ── the destination decision table ────────────────────────────────────────────


def test_egress_always_masks_even_with_no_policy() -> None:
    """An LLM context masks regardless of policy — it is not a policy question."""
    assert values_are_masked(None, destination=Destination.EGRESS) is True
    assert values_are_masked({}, destination=Destination.EGRESS) is True


def test_interactive_shows_values_by_default() -> None:
    """The point of the fix: an author's own preview does not lose its values.

    If this ever flips, the dry-run and profiler have silently gone back to
    trading a capability for a policy artifact.
    """
    assert values_are_masked(None, destination=Destination.INTERACTIVE) is False
    assert (
        values_are_masked({"pii_columns": ["email"]}, destination=Destination.INTERACTIVE) is False
    )


def test_interactive_masks_under_fail_closed() -> None:
    """G3 fail-closed still wins — the operator chose assurance over capability."""
    policy = {"require_classification": True}
    assert values_are_masked(policy, destination=Destination.INTERACTIVE) is True


# ── which columns are sensitive ───────────────────────────────────────────────


def test_policy_pii_column_is_sensitive() -> None:
    sensitive = sensitive_profile_columns(
        [_col("email"), _col("order_id")], policy={"pii_columns": ["email"]}
    )
    assert sensitive == ["email"]


def test_governance_tag_floor_is_honoured() -> None:
    """The G3 warehouse tag is the floor a suite policy cannot lift.

    This is the rung a second, hand-rolled sensitivity check would forget, which
    is why the seam delegates to `run_service` instead of re-deriving it.
    """
    sensitive = sensitive_profile_columns(
        [_col("field_7")], policy=None, tags={"field_7": "restricted"}
    )
    assert sensitive == ["field_7"]


def test_value_signal_is_used_not_just_the_column_name() -> None:
    """A harmless-looking name holding emails must still classify as sensitive.

    `_profile_sample_values` feeds `top_values`/min/max to the classifier for
    exactly this case. Passing an empty list would leave only the name heuristic
    and this column would sail through.
    """
    sensitive = sensitive_profile_columns(
        [_col("field_7", top=["ada@example.com", "bob@example.com"], lo=None, hi=None)],
        policy=None,
    )
    assert sensitive == ["field_7"]


# ── masking keeps the statistics ──────────────────────────────────────────────


def test_masking_removes_values_but_keeps_every_statistic() -> None:
    masked = mask_profile_columns([_col("email")], sensitive=["email"])[0]

    # Removed: real cell contents.
    assert masked.min_value is None
    assert masked.max_value is None
    assert [t["value"] for t in masked.top_values] == [_REDACTED_VALUE, _REDACTED_VALUE]

    # Kept: facts ABOUT the data. This half is the "no functionality lost"
    # promise; without it a masked profile answers nothing.
    assert masked.null_count == 3
    assert masked.null_fraction == 0.25
    assert masked.distinct_count == 7
    assert [t["count"] for t in masked.top_values] == [2, 2]


def test_non_sensitive_columns_pass_through_untouched() -> None:
    original = _col("order_id")
    out = mask_profile_columns([original, _col("email")], sensitive=["email"])
    assert out[0] is original


def test_masking_does_not_mutate_the_input() -> None:
    """A caller holding the original must not be able to leak through it."""
    original = _col("email")
    mask_profile_columns([original], sensitive=["email"])
    assert original.min_value == "a"
    assert [t["value"] for t in original.top_values] == ["x", "y"]


@pytest.mark.parametrize("sensitive", [[], None])
def test_empty_sensitive_list_is_a_passthrough(sensitive: list[str] | None) -> None:
    cols = [_col("a"), _col("b")]
    assert mask_profile_columns(cols, sensitive=sensitive or []) == cols
