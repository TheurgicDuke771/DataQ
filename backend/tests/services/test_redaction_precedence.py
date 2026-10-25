"""The redaction authority ladder, as a matrix — G3 / #433 AC-2."""

from __future__ import annotations

import pytest

from backend.app.services.run_service import _known_sensitive, _may_show_incidental

_SENSITIVE_TAG = {"weird_name": "pii"}
_CLEARED_TAG = {"weird_name": "public", "notes": "public"}


# ── the ladder, tested as an order rather than as examples ───────────────────


@pytest.mark.parametrize(
    ("tags", "policy", "shown", "why"),
    [
        (
            _SENSITIVE_TAG,
            {"identifier_column": "weird_name", "pii_columns": []},
            False,
            "a governance tag OUTRANKS the operator's explicit show — otherwise the "
            "floor set by whoever governs the warehouse is only a suggestion",
        ),
        (
            None,
            {"identifier_column": "weird_name", "pii_columns": ["weird_name"]},
            False,
            "an explicit mask outranks an explicit show by the same operator — the "
            "safe direction when they contradict themselves",
        ),
        (
            None,
            None,
            False,
            "with no authority at all, an unclassifiable name default-masks (#415) — "
            "the conservative default the classifier is built around",
        ),
    ],
)
def test_the_authority_ladder_holds_in_order(
    tags: dict[str, str] | None, policy: dict[str, object] | None, shown: bool, why: str
) -> None:
    """`weird_name` is deliberately meaningless to the name heuristic, so each row
    isolates the rung under test rather than the classifier's guess.
    """
    assert _may_show_incidental("weird_name", ["v1", "v2"], policy, tags) is shown, why


def test_a_designated_identifier_that_is_itself_pii_is_still_masked() -> None:
    """The one case where the operator's explicit show loses to a *guess*, and it
    is the right way round: an `EMAIL` column named as the row locator is direct
    PII whatever it was designated as.
    """
    policy = {"identifier_column": "email", "pii_columns": []}
    assert _may_show_incidental("email", ["a@b.com"], policy, None) is False


def test_an_explicit_identifier_shows_a_column_the_classifier_would_not() -> None:
    """The rung that makes `identifier_column` worth having."""
    assert _may_show_incidental("notes", ["v1", "v2"], None, None) is False
    policy = {"identifier_column": "notes", "pii_columns": []}
    assert _may_show_incidental("notes", ["v1", "v2"], policy, None) is True


def test_a_governance_tag_masks_the_tested_column_too() -> None:
    """`_known_sensitive` gates the tested and identifier columns — the ones shown
    by default because seeing the failing value is the point. The tag has to reach
    them as well, or the floor only applies to incidental columns.
    """
    assert _known_sensitive("weird_name", ["v"], None, _SENSITIVE_TAG) is True


# ── fail-closed: removes the guess, keeps the explicit ───────────────────────


def test_fail_closed_masks_a_column_the_classifier_would_have_cleared() -> None:
    """The exact risk G3 names, and it lands on the TESTED column — worth stating
    precisely, because measuring it corrected my assumption.
    """
    values = ["123-45-6789", "987-65-4321"]
    assert (
        _known_sensitive("field_7", values, None, None) is False
    ), "precondition: the classifier has no affirmative objection to this name"
    assert _known_sensitive("field_7", values, {"require_classification": True}, None) is True

    # The incidental path was already safe, and stays so — asserted rather than assumed, since
    # "fail-closed changed nothing here" is a fact about the default, not an omission.
    assert _may_show_incidental("field_7", values, None, None) is False
    assert _may_show_incidental("field_7", values, {"require_classification": True}, None) is False


def test_fail_closed_still_shows_an_explicitly_cleared_column() -> None:
    """It removes the classifier's permissive fallback, not every path. An
    operator's own `identifier_column` and a governance tag saying `public` are
    explicit clearances, and a mode that masked those too would make failing rows
    unactionable with no way back.
    """
    policy_id = {"require_classification": True, "identifier_column": "notes"}
    assert _may_show_incidental("notes", ["v1", "v2"], policy_id, None) is True

    policy_only = {"require_classification": True}
    assert _may_show_incidental("notes", ["v1", "v2"], policy_only, _CLEARED_TAG) is True


def test_a_clearance_does_not_beat_an_affirmative_pii_signal() -> None:
    """Fail-closed may only ever TIGHTEN, and this is where that nearly broke."""
    for policy in (
        {"require_classification": True, "identifier_column": "email"},
        {"require_classification": True},
    ):
        tags = None if "identifier_column" in policy else {"email": "public"}
        assert _may_show_incidental("email", ["a@b.com"], policy, tags) is False
        assert _known_sensitive("email", ["a@b.com"], policy, tags) is True

    # …and the default mode already behaved this way, so fail-closed is not
    # merely matching it — it must not be looser, which is the assertion above.
    assert _may_show_incidental("email", ["a@b.com"], {"identifier_column": "email"}, None) is False


def test_fail_closed_does_not_lift_the_governance_floor() -> None:
    """The mode only ever tightens. A tag saying sensitive still masks, and an
    explicit `pii_columns` entry still masks — otherwise "require classification"
    would be a way to *un*-mask by declaring an identifier.
    """
    policy = {"require_classification": True, "identifier_column": "weird_name"}
    assert _may_show_incidental("weird_name", ["v"], policy, _SENSITIVE_TAG) is False

    policy_pii = {"require_classification": True, "pii_columns": ["weird_name"]}
    assert _may_show_incidental("weird_name", ["v"], policy_pii, None) is False

    # …and on the TESTED-column path too.
    assert _known_sensitive("weird_name", ["v"], policy, _SENSITIVE_TAG) is True
    assert _known_sensitive("weird_name", ["v"], policy_pii, None) is True


def test_fail_closed_gates_the_tested_column_as_well() -> None:
    """The tested column is shown by default — seeing what failed is the point —
    so a fail-closed mode that only covered incidental columns would leave the
    single most likely place for an unclassified SSN wide open.
    """
    values = ["123-45-6789"]
    assert (
        _known_sensitive("field_7", values, None, None) is False
    ), "precondition: ordinarily the tested column is not known sensitive"
    assert _known_sensitive("field_7", values, {"require_classification": True}, None) is True
    # …and an explicit clearance still lets it through.
    cleared = {"require_classification": True, "identifier_column": "field_7"}
    assert _known_sensitive("field_7", values, cleared, None) is False


def test_an_internal_tag_is_not_a_clearance() -> None:
    """`internal` is a confidentiality LEVEL, not an assertion about personal data
    — and it is commonly the default stamp applied to everything.
    """
    policy = {"require_classification": True}
    assert _may_show_incidental("notes", ["v1", "v2"], policy, {"notes": "internal"}) is False
    # …while a value that really does assert non-personal data still clears.
    assert _may_show_incidental("notes", ["v1", "v2"], policy, {"notes": "public"}) is True


# ── the profiler detection rung (ADR 0049) ───────────────────────────────────

from backend.app.services.column_tags import DETECTED  # noqa: E402
from backend.app.services.run_service import value_signal_decides  # noqa: E402

# `field_7`, not `weird_name`: the `name` token is itself a PII name signal, which would mask
# at every rung and hide the one under test.
_DETECTED = {"field_7": DETECTED}
_SHOW_IT = {"identifier_column": "field_7", "pii_columns": []}


def test_a_persisted_detection_masks_an_incidental_column_the_classifier_would_clear() -> None:
    """Id-shaped values make the live classifier SHOW an unclassifiable column; a persisted
    detection (the values looked like PII at profile time) must still mask it."""
    uuid_like = ["0f8fafd4-9a1e-4b2e-9a4e-1b2c3d4e5f60", "1f8fafd4-9a1e-4b2e-9a4e-1b2c3d4e5f61"]
    assert _may_show_incidental("field_7", uuid_like, None, None) is True, "precondition"
    assert _may_show_incidental("field_7", uuid_like, None, _DETECTED) is False


def test_a_persisted_detection_masks_the_tested_column_too() -> None:
    assert _known_sensitive("field_7", ["v"], None, None) is False, "precondition"
    assert _known_sensitive("field_7", ["v"], None, _DETECTED) is True


def test_the_operators_identifier_column_clears_a_detection() -> None:
    """On a datasource with no warehouse tag this is a false positive's only exit, so it sits
    ABOVE the detection — unlike a governance tag, which it can never lift."""
    assert _may_show_incidental("field_7", ["v1", "v2"], _SHOW_IT, _DETECTED) is True
    assert _known_sensitive("field_7", ["v1", "v2"], _SHOW_IT, _DETECTED) is False
    # …but not a column whose VALUES are themselves affirmatively PII.
    assert _known_sensitive("field_7", ["a@b.com", "c@d.com"], _SHOW_IT, _DETECTED) is True


def test_a_detection_is_not_a_clearance_under_fail_closed() -> None:
    policy = {"require_classification": True}
    assert _may_show_incidental("field_7", ["v1"], policy, _DETECTED) is False
    assert _known_sensitive("field_7", ["v1"], policy, _DETECTED) is True


def test_pii_columns_still_masks_what_identifier_column_would_show() -> None:
    policy = {"identifier_column": "field_7", "pii_columns": ["field_7"]}
    assert _may_show_incidental("field_7", ["v1"], policy, _DETECTED) is False


def test_values_cannot_change_a_detected_columns_masking_unless_it_is_the_identifier() -> None:
    """The SQL lanes pay for a population sample only when values can decide (#2014)."""
    assert value_signal_decides("field_7", None, None) is True, "precondition"
    assert value_signal_decides("field_7", None, _DETECTED) is False
    assert value_signal_decides("field_7", _SHOW_IT, _DETECTED) is True


# ── every rung's decision, pinned as one table (#2255) ───────────────────────
#
# The reason-per-column work refactors the ladder to report WHICH rung decided. The refactor is
# allowed to change nothing about what is masked, so each rung's boolean verdict is pinned here
# first, on both gates, before the ladder is touched. A row is
# (column, values, policy, tags, known_sensitive, may_show_incidental).

from backend.app.services.run_service import (  # noqa: E402
    _redact_comparison_row,
    redact_sample_failures_with_state,
)

_UUIDS = ["0f8fafd4-9a1e-4b2e-9a4e-1b2c3d4e5f60", "1f8fafd4-9a1e-4b2e-9a4e-1b2c3d4e5f61"]
_FAIL_CLOSED: dict[str, object] = {"require_classification": True}

_RUNG_MATRIX: list[
    tuple[str, list[str], dict[str, object] | None, dict[str, str] | None, bool, bool]
] = [
    # own warehouse tag: masked on both gates, whatever the policy says
    ("field_7", ["v"], None, {"field_7": "sensitive"}, True, False),
    ("field_7", ["v"], {"identifier_column": "field_7"}, {"field_7": "pii"}, True, False),
    # an inherited tag arrives in the effective map as plain `sensitive` — same verdict
    ("field_7", ["v"], {"identifier_column": "field_7"}, {"field_7": "sensitive"}, True, False),
    # suite policy `pii_columns`: masked on both gates, beats `identifier_column`
    ("field_7", ["v"], {"pii_columns": ["field_7"]}, None, True, False),
    (
        "field_7",
        ["v"],
        {"pii_columns": ["FIELD_7"], "identifier_column": "field_7"},
        None,
        True,
        False,
    ),
    # persisted profiler detection (ADR 0049): masked unless THIS column is the identifier
    ("field_7", _UUIDS, None, {"field_7": DETECTED}, True, False),
    ("field_7", ["v1", "v2"], _SHOW_IT, {"field_7": DETECTED}, False, True),
    # read-time classifier — by name
    ("email", ["v"], None, None, True, False),
    ("email", ["v"], {"identifier_column": "email"}, None, True, False),
    # read-time classifier — by value
    ("field_7", ["a@b.com", "c@d.com"], None, None, True, False),
    ("field_7", ["a@b.com", "c@d.com"], _SHOW_IT, None, True, False),
    # default mask: an unclassifiable incidental column masks; the tested column shows
    ("field_7", ["v1", "v2"], None, None, False, False),
    ("field_7", ["v1", "v2"], {"pii_columns": []}, None, False, False),
    # affirmatively safe / identifier-shaped values: shown on both gates
    ("field_7", _UUIDS, None, None, False, True),
    ("order_id", ["1", "2"], None, None, False, True),
    ("field_7", ["v1", "v2"], _SHOW_IT, None, False, True),
    # a `public` tag is consulted only in fail-closed mode; the default mode still default-masks
    ("field_7", ["v1", "v2"], None, {"field_7": "public"}, False, False),
    # fail-closed: masked without clearance, shown with it, classifier still wins
    ("field_7", ["v1"], _FAIL_CLOSED, None, True, False),
    ("field_7", ["v1"], _FAIL_CLOSED, {"field_7": "internal"}, True, False),
    ("field_7", ["v1"], _FAIL_CLOSED, {"field_7": DETECTED}, True, False),
    ("field_7", ["v1"], _FAIL_CLOSED, {"field_7": "public"}, False, True),
    ("field_7", ["v1"], {**_FAIL_CLOSED, "identifier_column": "field_7"}, None, False, True),
    ("field_7", ["a@b.com"], _FAIL_CLOSED, {"field_7": "public"}, True, False),
    (
        "field_7",
        ["v1"],
        {**_FAIL_CLOSED, "pii_columns": ["field_7"]},
        {"field_7": "public"},
        True,
        False,
    ),
    # the column-less scalar path runs the ladder under an empty name
    ("", ["42"], None, None, False, False),
    ("", ["42"], _FAIL_CLOSED, None, True, False),
]


@pytest.mark.parametrize(("column", "values", "policy", "tags", "known", "shown"), _RUNG_MATRIX)
def test_every_rung_decision_is_pinned(
    column: str,
    values: list[str],
    policy: dict[str, object] | None,
    tags: dict[str, str] | None,
    known: bool,
    shown: bool,
) -> None:
    assert _known_sensitive(column, values, policy, tags) is known
    assert _may_show_incidental(column, values, policy, tags) is shown


def test_comparison_rows_pin_the_suffixed_hard_mask() -> None:
    """Both sides of a policy column mask on the SUFFIX-STRIPPED name; a suffixed raw name
    that is itself tagged hard-masks; the join key shows."""
    row = {"order_id": 7, "email_src": "a@b.com", "email_tgt": "a@b.com", "notes_src": "x"}
    vbc = {k: [v] for k, v in row.items()}
    out = _redact_comparison_row(
        row,
        policy={"pii_columns": ["notes"]},
        tags={"email_src": "sensitive"},
        values_by_column=vbc,
    )
    assert out == {
        "order_id": 7,
        "email_src": "<redacted>",
        "email_tgt": "<redacted>",
        "notes_src": "<redacted>",
    }


def test_the_redacted_column_set_of_a_mixed_row_is_pinned() -> None:
    sample = {
        "unexpected_count": 2,
        "unexpected_index_list": [
            {"order_id": "ORD-1", "email": "a@b.com", "field_7": "v1", "ssn_hash": "x", "amt": 1},
            {"order_id": "ORD-2", "email": "c@d.com", "field_7": "v2", "ssn_hash": "y", "amt": 2},
        ],
    }
    redacted, state, columns = redact_sample_failures_with_state(
        sample,
        tested_column="amt",
        policy={"identifier_column": "order_id", "pii_columns": ["ssn_hash"]},
        tags={"field_7": DETECTED},
    )[:3]
    assert state == "partial"
    assert columns == ["email", "field_7", "ssn_hash"]
    assert redacted is not None
    assert redacted["unexpected_index_list"][0] == {
        "order_id": "ORD-1",
        "email": "<redacted>",
        "field_7": "<redacted>",
        "ssn_hash": "<redacted>",
        "amt": 1,
    }
