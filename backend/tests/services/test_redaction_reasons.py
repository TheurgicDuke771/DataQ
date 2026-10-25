"""Which rung masked a column (#2255) — the reason beside every `redacted_columns` entry.

The ladder's booleans are pinned in `test_redaction_precedence`; here each rung's REASON is
asserted, and the two are tied together so the reason twins can never drift from the verdicts.
"""

from __future__ import annotations

import pytest

from backend.app.services.classification_vocab import DETECTED, EffectiveColumnTags
from backend.app.services.run_service import (
    REDACTION_REASONS,
    _incidental_mask_reason,
    _known_sensitive,
    _known_sensitive_reason,
    _may_show_incidental,
    _redact_comparison_row,
    _RedactionTracker,
    _tag_mask_reason,
    redact_sample_failures_with_state,
)
from backend.tests.services.test_redaction_precedence import _RUNG_MATRIX

_INHERITED = EffectiveColumnTags({"field_7": "sensitive"}, inherited=frozenset({"field_7"}))
_OWN = EffectiveColumnTags({"field_7": "sensitive"}, inherited=frozenset())


@pytest.mark.parametrize(("column", "values", "policy", "tags", "known", "shown"), _RUNG_MATRIX)
def test_the_reason_twins_agree_with_the_pinned_verdicts(
    column: str,
    values: list[str],
    policy: dict[str, object] | None,
    tags: dict[str, str] | None,
    known: bool,
    shown: bool,
) -> None:
    """Every row of the pinned matrix: a reason exactly when the boolean masks, and the
    reason is one of the closed vocabulary."""
    known_reason = _known_sensitive_reason(column, values, policy, tags)
    incidental_reason = _incidental_mask_reason(column, values, policy, tags)
    assert (known_reason is not None) is known is _known_sensitive(column, values, policy, tags)
    assert (
        (incidental_reason is None) is shown is _may_show_incidental(column, values, policy, tags)
    )
    for reason in (known_reason, incidental_reason):
        assert reason is None or reason in REDACTION_REASONS


# ── one rung, one reason ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("column", "values", "policy", "tags", "reason"),
    [
        ("field_7", ["v"], {"identifier_column": "field_7"}, _OWN, "own_tag"),
        ("field_7", ["v"], {"identifier_column": "field_7"}, _INHERITED, "inherited_tag"),
        # a plain map (no provenance) reads as the column's own tag
        ("field_7", ["v"], None, {"field_7": "pii"}, "own_tag"),
        ("field_7", ["v"], {"pii_columns": ["FIELD_7"]}, None, "policy"),
        ("field_7", ["v"], {"require_classification": True}, None, "unclassified"),
        (
            "field_7",
            ["v"],
            {"require_classification": True},
            {"field_7": "internal"},
            "unclassified",
        ),
        ("field_7", ["v"], None, {"field_7": DETECTED}, "detected"),
        ("email", ["v"], None, None, "classifier"),
        ("field_7", ["a@b.com", "c@d.com"], None, None, "classifier"),
        # fail-closed + cleared + affirmative PII values: the classifier, not the mode
        (
            "field_7",
            ["a@b.com"],
            {"require_classification": True},
            {"field_7": "public"},
            "classifier",
        ),
        # identifier override + affirmative PII values: the classifier
        (
            "field_7",
            ["a@b.com"],
            {"identifier_column": "field_7"},
            {"field_7": DETECTED},
            "classifier",
        ),
    ],
)
def test_each_rung_names_itself(
    column: str,
    values: list[str],
    policy: dict[str, object] | None,
    tags: dict[str, str] | None,
    reason: str,
) -> None:
    assert _known_sensitive_reason(column, values, policy, tags) == reason
    assert _incidental_mask_reason(column, values, policy, tags) == reason


def test_the_default_mask_is_the_incidental_gate_only() -> None:
    """An unclassifiable incidental column masks as a precaution; the tested column shows."""
    assert _incidental_mask_reason("field_7", ["v1", "v2"], None, None) == "default_mask"
    assert _known_sensitive_reason("field_7", ["v1", "v2"], None, None) is None


def test_the_floor_outranks_every_other_reason() -> None:
    """A column that several rungs would mask reports the HIGHEST one — the one a reader has
    to change to lift it."""
    tags = EffectiveColumnTags({"email": "sensitive"}, inherited=frozenset({"email"}))
    policy = {"pii_columns": ["email"], "require_classification": True}
    assert _known_sensitive_reason("email", ["a@b.com"], policy, tags) == "inherited_tag"
    assert _incidental_mask_reason("email", ["a@b.com"], policy, tags) == "inherited_tag"
    assert _known_sensitive_reason("email", ["a@b.com"], policy, None) == "policy"


def test_tag_provenance_folds_the_column_name() -> None:
    tags = EffectiveColumnTags({"email": "sensitive"}, inherited=frozenset({"email"}))
    assert _tag_mask_reason("EMAIL", tags) == "inherited_tag"
    assert _tag_mask_reason("email ", tags) == "inherited_tag"
    assert _tag_mask_reason("other", tags) is None


# ── the tracker and the sample summary ───────────────────────────────────────


def test_reasons_share_the_key_set_of_redacted_columns_and_the_masked_set_is_unchanged() -> None:
    sample = {
        "unexpected_count": 2,
        "unexpected_index_list": [
            {"order_id": "ORD-1", "email": "a@b.com", "field_7": "v1", "ssn_hash": "x", "amt": 1},
            {"order_id": "ORD-2", "email": "c@d.com", "field_7": "v2", "ssn_hash": "y", "amt": 2},
        ],
    }
    out = redact_sample_failures_with_state(
        sample,
        tested_column="amt",
        policy={"identifier_column": "order_id", "pii_columns": ["ssn_hash"]},
        tags={"field_7": DETECTED},
    )
    # The same masked set `test_redaction_precedence` pins for this sample…
    assert out.state == "partial"
    assert out.redacted_columns == ["email", "field_7", "ssn_hash"]
    # …now with a reason each, keyed identically.
    assert out.redaction_reasons == {
        "email": "classifier",
        "field_7": "detected",
        "ssn_hash": "policy",
    }
    assert list(out.redaction_reasons) == out.redacted_columns


def test_a_masked_tested_scalar_list_carries_its_reason() -> None:
    out = redact_sample_failures_with_state(
        {"partial_unexpected_list": ["a@b.com"]},
        tested_column="email",
        policy={"pii_columns": ["email"]},
    )
    assert out.redacted_columns == ["email"]
    assert out.redaction_reasons == {"email": "policy"}


def test_a_zero_sample_result_has_no_reasons() -> None:
    out = redact_sample_failures_with_state(None, zero_sample=True)
    assert out.state == "zero_sample"
    assert out.redacted_columns == [] and out.redaction_reasons == {}


def test_a_column_shown_anywhere_has_no_reason() -> None:
    """`redacted_columns` lists columns masked EVERYWHERE they appeared; a column shown in one
    bucket and masked in another is partial, not redacted, so it carries no reason."""
    tracker = _RedactionTracker()
    tracker.record("notes", False, "default_mask")
    tracker.record("notes", True)
    tracker.record("email", False, "classifier")
    assert tracker.summary() == ("partial", ["email"])
    assert tracker.reasons() == {"email": "classifier"}


def test_an_unrecorded_reason_is_omitted_never_reported_as_default_mask() -> None:
    """#2548 review: `default_mask` asserts "never classified"; a column masked with no recorded
    rung may well have been classified, so it is logged and left out rather than mislabelled."""
    tracker = _RedactionTracker()
    tracker.record("x", False)
    assert tracker.summary() == ("full", ["x"])
    assert tracker.reasons() == {}


def test_the_strongest_reason_wins_across_comparison_buckets() -> None:
    """#2548 review: a comparison sample runs the ladder per bucket, so one column can be
    `default_mask` in one bucket and `classifier` in another; the affirmative verdict wins
    whatever order the buckets arrive in."""
    for order in (("default_mask", "classifier"), ("classifier", "default_mask")):
        tracker = _RedactionTracker()
        for reason in order:
            tracker.record("contact", False, reason)
        assert tracker.reasons() == {"contact": "classifier"}


def test_reason_vocabulary_has_one_source() -> None:
    from typing import get_args

    from backend.app.services.run_service import RedactionReason

    assert REDACTION_REASONS == get_args(RedactionReason)


def test_copy_tags_keeps_lineage_provenance() -> None:
    """#2548 review: `dict(tags)` drops the inherited set, so an inherited mask would report as
    `own_tag`; `copy_tags` is the copy every caller that mutates the map uses."""
    from backend.app.services.classification_vocab import copy_tags, inherited_tag_columns

    copied = copy_tags(_INHERITED)
    copied["other"] = DETECTED
    assert inherited_tag_columns(copied) == frozenset({"field_7"})
    assert _tag_mask_reason("field_7", copied) == "inherited_tag"
    assert inherited_tag_columns(_INHERITED) == frozenset({"field_7"})  # source untouched
    assert "other" not in _INHERITED
    assert copy_tags(None) == {}


@pytest.mark.parametrize(
    ("column", "values"),
    [
        ("email", ["a@b.com"]),  # PII by name
        ("contact", ["a@b.com", "c@d.org"]),  # PII by value
        ("field_7", ["zq", "xk"]),  # unplaceable: the default mask
        ("order_id", [1, 2, 3]),  # identifier
        ("status", ["open", "closed"]),
    ],
)
def test_classify_with_basis_matches_classify_and_is_sensitive(
    column: str, values: list[object]
) -> None:
    """The single-pass basis must equal the two calls it replaces on the default path."""
    from backend.app.services.column_classification import (
        classify_column,
        classify_column_with_basis,
        is_sensitive,
    )

    verdict, affirmative = classify_column_with_basis(column, values)
    assert verdict is classify_column(column, values)
    assert affirmative is is_sensitive(column, values)


def test_comparison_rows_name_the_rung_per_side() -> None:
    row = {"order_id": 7, "email_src": "a@b.com", "email_tgt": "a@b.com", "notes_src": "x"}
    tracker = _RedactionTracker()
    _redact_comparison_row(
        row,
        policy={"pii_columns": ["notes"]},
        tags={"email_src": "sensitive"},
        values_by_column={k: [v] for k, v in row.items()},
        tracker=tracker,
    )
    assert tracker.summary() == ("partial", ["email_src", "email_tgt", "notes_src"])
    assert tracker.reasons() == {
        "email_src": "own_tag",  # the suffixed raw name is what the tag names
        "email_tgt": "classifier",  # the stripped name, through the incidental ladder
        "notes_src": "policy",
    }
