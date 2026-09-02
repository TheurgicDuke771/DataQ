"""Unit tests for the live-probe disclosure seam (#1419, #1479)."""

from __future__ import annotations

import pytest

from backend.app.services.live_probe import (
    Destination,
    applicable_tags,
    mask_profile_columns,
    redact_probe_observed_value,
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
    """The point of the fix: an author's own preview does not lose its values."""
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
    """The G3 warehouse tag is the floor a suite policy cannot lift."""
    sensitive = sensitive_profile_columns(
        [_col("field_7")], policy=None, tags={"field_7": "restricted"}
    )
    assert sensitive == ["field_7"]


def test_value_signal_is_used_not_just_the_column_name() -> None:
    """A harmless-looking name holding emails must still classify as sensitive."""
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


# ── review findings on the seam itself (PR #1481) ─────────────────────────────


def test_applicable_tags_keeps_the_sensitive_floor_on_an_off_asset_probe() -> None:
    """F2: dropping the whole tag map masks LESS, not more."""
    tags = {"email": "restricted", "order_id": "public"}
    kept = applicable_tags(tags, probed_other_target=True)
    assert kept == {"email": "restricted"}  # floor kept, clearance dropped


def test_applicable_tags_are_untouched_for_the_suites_own_asset() -> None:
    tags = {"email": "restricted", "order_id": "public"}
    assert applicable_tags(tags, probed_other_target=False) == tags


def test_scalar_observed_value_is_masked() -> None:
    """F4: `run_service.redact_observed_value` handles lists, not scalars."""
    policy = {"pii_columns": ["email"]}
    out = redact_probe_observed_value(
        {"observed_value": "ada@example.com"}, tested_column="email", policy=policy
    )
    assert out == {"observed_value": _REDACTED_VALUE}


def test_columnless_scalar_is_screened_through_the_expectation_type() -> None:
    """#1793: the dry-run doors forward `expectation_type`, so a custom-SQL preview (no
    `config.column`) still runs the value-shape signal instead of skipping the ladder.
    """
    out = redact_probe_observed_value(
        {"observed_value": "ada@example.com"},
        tested_column=None,
        policy=None,
        expectation_type="unexpected_rows_expectation",
    )
    assert out == {"observed_value": _REDACTED_VALUE}
    count = redact_probe_observed_value(
        {"observed_value": 3},
        tested_column=None,
        policy=None,
        expectation_type="unexpected_rows_expectation",
    )
    assert count == {"observed_value": 3}


def test_scalar_observed_value_on_an_unflagged_column_is_untouched() -> None:
    out = redact_probe_observed_value(
        {"observed_value": 34680}, tested_column="order_id", policy=None
    )
    assert out == {"observed_value": 34680}


def test_egress_default_masks_an_unclassifiable_column_and_interactive_does_not() -> None:
    """F5: the rung differs by destination — the same principle, one level deeper."""
    cols = [_col("notes", top=["lorem ipsum", "dolor sit"], lo=None, hi=None)]
    assert sensitive_profile_columns(cols, policy=None) == []
    assert sensitive_profile_columns(cols, policy=None, destination=Destination.EGRESS) == ["notes"]
