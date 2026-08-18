"""The redaction authority ladder, as a matrix — G3 / #433 AC-2.

Four sources can decide whether a value is surfaced, and they are NOT equal:

1. **A datasource governance tag** — the floor. Set by whoever governs the
   warehouse, and an application-level override must not be able to lift it.
2. **The suite's `pii_columns`** — the operator's explicit "mask this".
3. **The suite's `identifier_column`** — the operator's explicit "show this",
   which still loses to 1 and 2, and to a value that is *itself* direct PII.
4. **The name/value classifier** — a guess, and only consulted when nothing above
   has an opinion.

Each rung existed before this file; what did not exist was a test that pins the
ORDER. Two isolated cases were covered, so a change that made the policy override
the tag — the one inversion that turns a governance floor into a suggestion —
would have passed. That is the whole reason for a matrix rather than more
examples.

`require_classification` (fail-closed, G3) is the fifth row: it does not reorder
the ladder, it removes rung 4. See the second half of the file.
"""

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
    isolates the rung under test rather than the classifier's guess."""
    assert _may_show_incidental("weird_name", ["v1", "v2"], policy, tags) is shown, why


def test_a_designated_identifier_that_is_itself_pii_is_still_masked() -> None:
    """The one case where the operator's explicit show loses to a *guess*, and it
    is the right way round: an `EMAIL` column named as the row locator is direct
    PII whatever it was designated as."""
    policy = {"identifier_column": "email", "pii_columns": []}
    assert _may_show_incidental("email", ["a@b.com"], policy, None) is False


def test_an_explicit_identifier_shows_a_column_the_classifier_would_not() -> None:
    """The rung that makes `identifier_column` worth having.

    `notes` classifies as PII (the conservative default for a name the heuristic
    cannot place) yet is not *affirmatively* sensitive, so as an incidental column
    it default-masks. Naming it the row locator is the operator supplying
    the authority the classifier lacks — distinct from the `EMAIL` case above,
    where the classifier has an affirmative objection rather than merely no
    opinion. Both cases were needed: without this one, "identifier loses" would
    read as the whole rule.
    """
    assert _may_show_incidental("notes", ["v1", "v2"], None, None) is False
    policy = {"identifier_column": "notes", "pii_columns": []}
    assert _may_show_incidental("notes", ["v1", "v2"], policy, None) is True


def test_a_governance_tag_masks_the_tested_column_too() -> None:
    """`_known_sensitive` gates the tested and identifier columns — the ones shown
    by default because seeing the failing value is the point. The tag has to reach
    them as well, or the floor only applies to incidental columns."""
    assert _known_sensitive("weird_name", ["v"], None, _SENSITIVE_TAG) is True


# ── fail-closed: removes the guess, keeps the explicit ───────────────────────


def test_fail_closed_masks_a_column_the_classifier_would_have_cleared() -> None:
    """The exact risk G3 names, and it lands on the TESTED column — worth stating
    precisely, because measuring it corrected my assumption.

    An *incidental* `field_7` already default-masks (#415), so the hole is not
    there. But the **tested** column is shown by design — seeing the failing value
    is the point of a sample — and it is gated by `_known_sensitive`, the
    *affirmative* test. `field_7` full of SSNs is not affirmatively anything to a
    name heuristic, so it surfaces. That is the false negative G3 is about, and
    fail-closed is what closes it: a guess is not an authority.
    """
    values = ["123-45-6789", "987-65-4321"]
    assert (
        _known_sensitive("field_7", values, None, None) is False
    ), "precondition: the classifier has no affirmative objection to this name"
    assert _known_sensitive("field_7", values, {"require_classification": True}, None) is True

    # The incidental path was already safe, and stays so — asserted rather than
    # assumed, since "fail-closed changed nothing here" is a fact about the
    # default, not an omission.
    assert _may_show_incidental("field_7", values, None, None) is False
    assert _may_show_incidental("field_7", values, {"require_classification": True}, None) is False


def test_fail_closed_still_shows_an_explicitly_cleared_column() -> None:
    """It removes the classifier's permissive fallback, not every path. An
    operator's own `identifier_column` and a governance tag saying `public` are
    explicit clearances, and a mode that masked those too would make failing rows
    unactionable with no way back.

    `notes` is used rather than a name the classifier objects to — see the next
    test for why that distinction is load-bearing.
    """
    policy_id = {"require_classification": True, "identifier_column": "notes"}
    assert _may_show_incidental("notes", ["v1", "v2"], policy_id, None) is True

    policy_only = {"require_classification": True}
    assert _may_show_incidental("notes", ["v1", "v2"], policy_only, _CLEARED_TAG) is True


def test_a_clearance_does_not_beat_an_affirmative_pii_signal() -> None:
    """Fail-closed may only ever TIGHTEN, and this is where that nearly broke.

    The first version returned on the clearance alone, so an `identifier_column`
    holding emails was **shown** in fail-closed mode and **masked** in the default
    one — the mode became a way to un-mask. A false safety guarantee is worse than
    no guarantee, and it was produced by the very change meant to add one.

    Both doors, and both directions: an explicit clearance is necessary for a
    value to surface here, and never sufficient on its own.
    """
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
    would be a way to *un*-mask by declaring an identifier."""
    policy = {"require_classification": True, "identifier_column": "weird_name"}
    assert _may_show_incidental("weird_name", ["v"], policy, _SENSITIVE_TAG) is False

    policy_pii = {"require_classification": True, "pii_columns": ["weird_name"]}
    assert _may_show_incidental("weird_name", ["v"], policy_pii, None) is False

    # …and on the TESTED-column path too. Asserted separately because the two
    # functions check the floor at different points, so a reordering can lift it
    # in one while leaving the other correct — which is exactly what a mutation
    # of `_known_sensitive` alone demonstrated: with the fail-closed branch moved
    # ABOVE the tag check, a governance-tagged column named as the identifier
    # came back "not sensitive" and would have surfaced.
    assert _known_sensitive("weird_name", ["v"], policy, _SENSITIVE_TAG) is True
    assert _known_sensitive("weird_name", ["v"], policy_pii, None) is True


def test_fail_closed_gates_the_tested_column_as_well() -> None:
    """The tested column is shown by default — seeing what failed is the point —
    so a fail-closed mode that only covered incidental columns would leave the
    single most likely place for an unclassified SSN wide open."""
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

    Accepting it as a clearance would un-mask whole tables in exactly the
    deployments careful enough to tag them, which is the opposite of what tagging
    is for. Asserted by name because the mistake is easy to make from the word
    alone: "internal" sounds restrictive, and as a clearance it is the loosest
    value in the vocabulary.
    """
    policy = {"require_classification": True}
    assert _may_show_incidental("notes", ["v1", "v2"], policy, {"notes": "internal"}) is False
    # …while a value that really does assert non-personal data still clears.
    assert _may_show_incidental("notes", ["v1", "v2"], policy, {"notes": "public"}) is True
