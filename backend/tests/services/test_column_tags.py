"""Warehouse column tags — G3 / #433."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from backend.app.services import column_tags as ct

# ── the documented convention ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("sensitive", ct.SENSITIVE),
        ("pii", ct.SENSITIVE),
        ("confidential", ct.SENSITIVE),
        ("restricted", ct.SENSITIVE),
        ("secret", ct.SENSITIVE),
        ("public", ct.NON_SENSITIVE),
        ("non_sensitive", ct.NON_SENSITIVE),
        ("nonsensitive", ct.NON_SENSITIVE),
        ("  PII  ", ct.SENSITIVE),
        ("Public", ct.NON_SENSITIVE),
    ],
)
def test_the_documented_vocabulary(value: str, expected: str) -> None:
    """Case- and whitespace-insensitive, because a tag is typed by a human in a
    warehouse UI and `Public ` is the same statement as `public`.
    """
    assert ct.normalize_tag(ct.DATAQ_TAG_KEY, value) == expected


@pytest.mark.parametrize("value", ["internal", "restricted-ish", "level-3", "", "yes", "1"])
def test_an_unrecognised_value_is_ignored_not_guessed(value: str) -> None:
    """`None` — no opinion — so the column falls through to the suite policy and
    then the classifier, exactly as if it were untagged.
    """
    assert ct.normalize_tag(ct.DATAQ_TAG_KEY, value) is None


def test_an_unrecognised_tag_KEY_is_ignored() -> None:
    """Only the documented key and Snowflake's own system tag are read."""
    assert ct.normalize_tag("cost_centre", "public") is None
    assert ct.normalize_tag("visibility", "public") is None


# ── Snowflake's own classification ───────────────────────────────────────────


@pytest.mark.parametrize("value", ["IDENTIFIER", "QUASI_IDENTIFIER", "SENSITIVE"])
def test_snowflake_privacy_category_always_masks(value: str) -> None:
    """All three of Snowflake's privacy categories denote personal data."""
    assert ct.normalize_tag("PRIVACY_CATEGORY", value) == ct.SENSITIVE


def test_snowflake_privacy_category_never_clears() -> None:
    """It has no clearance side, and inventing one would be wrong."""
    assert ct.normalize_tag("PRIVACY_CATEGORY", "PUBLIC") is None
    assert ct.normalize_tag("PRIVACY_CATEGORY", "NONE") is None


# ── conflicts and shapes ─────────────────────────────────────────────────────


def test_sensitive_wins_a_contradictory_pair() -> None:
    """A column tagged both ways is a governance contradiction, and the
    resolution is not arbitrary.
    """
    rows_public_first = [
        ("EMAIL", ct.DATAQ_TAG_KEY, "public"),
        ("EMAIL", ct.DATAQ_TAG_KEY, "pii"),
    ]
    rows_pii_first = list(reversed(rows_public_first))
    assert ct._rows_to_tags(rows_public_first) == {"email": ct.SENSITIVE}
    assert ct._rows_to_tags(rows_pii_first) == {"email": ct.SENSITIVE}


def test_column_names_are_lower_cased_to_match_the_redactor() -> None:
    """Snowflake returns identifiers upper-cased; the redactor matches on the
    lower-cased name. A map keyed `EMAIL` would silently match nothing — present,
    plausible, and completely inert.
    """
    assert ct._rows_to_tags([("EMAIL", ct.DATAQ_TAG_KEY, "pii")]) == {"email": ct.SENSITIVE}


def test_rows_with_no_recognised_tag_produce_no_entry() -> None:
    """An empty map, not an entry with a null verdict — the redactor's contract is
    "a column is in this map or it is not".
    """
    assert ct._rows_to_tags([("EMAIL", "cost_centre", "finance")]) == {}


def test_a_datasource_with_no_tag_concept_returns_none(monkeypatch: Any) -> None:
    """Only Snowflake and Unity Catalog have column tags. For ADLS, S3, Iceberg
    and flat files there is no source to read, so the answer is `None` — "could
    not look" — and no connection is opened, since there is nothing to ask.
    """

    class _Conn:
        type = "s3"
        config: ClassVar[dict[str, Any]] = {}

    def _explode(*_a: Any, **_k: Any) -> Any:  # pragma: no cover - must not run
        raise AssertionError("no connection may be opened for an untaggable type")

    monkeypatch.setattr(
        "backend.app.services.profile_service._open_connection", _explode, raising=False
    )
    assert ct.fetch_column_tags(_Conn(), table="t", secret_store=object()) is None  # type: ignore[arg-type]


def test_a_failing_lookup_is_silence_not_a_guess(monkeypatch: Any) -> None:
    """The safety property the whole module rests on."""

    class _Conn:
        type = "snowflake"
        config: ClassVar[dict[str, Any]] = {"database": "ANALYTICS"}

    def _explode(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("insufficient privileges on tag")

    monkeypatch.setattr(
        "backend.app.services.profile_service._open_connection", _explode, raising=False
    )
    assert ct.fetch_column_tags(_Conn(), table="ORDERS", secret_store=object()) is None  # type: ignore[arg-type]


def test_snowflake_binds_the_object_name_rather_than_interpolating_it() -> None:
    """The table function takes the object as a STRING argument, so it is bound."""
    stmt = ct._snowflake_query(database="ANALYTICS", schema="RETAIL", table="ORDERS")
    compiled = str(stmt)
    assert "ANALYTICS.RETAIL.ORDERS" not in compiled, "the object name must not be inlined"
    assert stmt.compile().params["obj"] == "ANALYTICS.RETAIL.ORDERS"
