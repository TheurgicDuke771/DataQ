"""Alerts carry which sample columns were masked and why (#2255), like REST and MCP do."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from backend.app.alerting import builder, card, render
from backend.app.alerting.base import CheckReport
from backend.app.alerting.webhook import _check_payload
from backend.tests.alerting.test_builder import _suite_with_check


def _check(**overrides: Any) -> CheckReport:
    base = CheckReport("c", "expect_x", "fail", None, None, None, None)
    return replace(base, **overrides)


def test_the_note_names_each_masked_column_with_its_reason() -> None:
    check = _check(
        redacted_columns=("email", "notes"),
        redaction_reasons={"email": "inherited_tag", "notes": "default_mask"},
    )
    assert render.check_redaction_note(check) == (
        "masked: email (tag inherited through lineage), "
        "notes (never classified, masked as a precaution)"
    )


def test_the_note_is_empty_when_nothing_was_masked() -> None:
    assert render.check_redaction_note(_check()) == ""


def test_an_unknown_reason_is_shown_verbatim_not_dropped() -> None:
    check = _check(redacted_columns=("x",), redaction_reasons={"x": "new_rung"})
    assert render.check_redaction_note(check) == "masked: x (new_rung)"
    assert render.check_redaction_note(_check(redacted_columns=("x",))) == "masked: x"


def test_check_detail_appends_the_note_last() -> None:
    check = _check(
        sample_summary={"unexpected_count": 4, "partial_unexpected_list": ["<redacted>"]},
        redacted_columns=("email",),
        redaction_reasons={"email": "own_tag"},
    )
    detail = render.check_detail(check)
    assert detail == "4 unexpected · e.g. <redacted> · masked: email (warehouse tag)"


def test_the_teams_card_block_carries_the_note() -> None:
    check = _check(
        observed_value={"observed_value": 1},
        expected_value={"min": 2},
        redacted_columns=("email",),
        redaction_reasons={"email": "policy"},
    )
    text = card._check_block(check)["text"]
    assert text.endswith(" · masked: email (suite policy)")


def test_the_webhook_payload_carries_both_fields() -> None:
    payload = _check_payload(
        _check(redacted_columns=("email",), redaction_reasons={"email": "classifier"})
    )
    assert payload["redacted_columns"] == ["email"]
    assert payload["redaction_reasons"] == {"email": "classifier"}
    assert _check_payload(_check())["redacted_columns"] == []


def test_the_builder_fills_them_from_the_same_ladder_as_rest(db_session: Any) -> None:
    raw = {"unexpected_count": 2, "partial_unexpected_list": ["alice@secret.com", "b@s.com"]}
    _suite, run = _suite_with_check(db_session, status="critical", sample=raw, column="email")
    report = builder.build_run_report(db_session, run).checks[0]
    assert report.sample_summary is not None
    assert report.sample_summary["partial_unexpected_list"] == ["<redacted>", "<redacted>"]
    assert report.redacted_columns == ("email",)
    assert report.redaction_reasons == {"email": "classifier"}


def test_the_note_caps_a_wide_table_and_points_at_the_run_page() -> None:
    """#2548 review: Slack refuses a section over 3000 chars and the whole alert is then lost, so
    the note names a bounded number of columns."""
    columns = tuple(f"column_with_a_long_name_{i:02d}" for i in range(40))
    check = _check(
        redacted_columns=columns, redaction_reasons=dict.fromkeys(columns, "default_mask")
    )
    note = render.check_redaction_note(check)
    assert note.endswith("+35 more on the run page")
    assert note.count("(never classified") == 5
    assert len(note) < 600
