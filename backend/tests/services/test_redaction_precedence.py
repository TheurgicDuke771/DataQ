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
