"""Opaque list cursors (#1987): what they encode, what they refuse, and that the seek they
build is one index descent however deep the page."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text

from backend.app.db.models import Run
from backend.app.services import paging

MOMENT = datetime(2026, 11, 10, 14, 37, 12, 123456, tzinfo=UTC)
ROW = uuid.UUID("11111111-1111-4111-8111-111111111111")


def _forge(**fields: Any) -> str:
    payload = {"v": 1, "k": "runs", "t": MOMENT.isoformat(), "i": str(ROW), **fields}
    raw = json.dumps({k: v for k, v in payload.items() if v is not ...}).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def test_a_cursor_round_trips_to_the_microsecond() -> None:
    token = paging.encode_cursor("runs", MOMENT, ROW)
    assert paging.decode_cursor("runs", token) == (MOMENT, ROW)
    assert "=" not in token  # safe in a query string as it is


def test_a_cursor_in_another_time_zone_names_the_same_instant() -> None:
    local = MOMENT.astimezone(timezone_plus_5_30())
    assert paging.decode_cursor("runs", paging.encode_cursor("runs", local, ROW))[0] == MOMENT


def timezone_plus_5_30() -> Any:
    from datetime import timezone

    return timezone(timedelta(hours=5, minutes=30))


@pytest.mark.parametrize(
    "token",
    [
        "",
        "not a cursor",
        "!!!!",
        base64.urlsafe_b64encode(b"not json").decode(),
        base64.urlsafe_b64encode(b"[1, 2]").decode(),
        _forge(v=2),  # a version this build does not know
        _forge(v=...),
        _forge(t="not a time"),
        _forge(t="2026-11-10T14:37:12"),  # no time zone
        _forge(i="not-a-uuid"),
        _forge(i=...),
        _forge(t=...),
    ],
    ids=[
        "empty",
        "plain text",
        "bad base64",
        "not json",
        "not an object",
        "other version",
        "no version",
        "bad time",
        "naive time",
        "bad id",
        "no id",
        "no time",
    ],
)
def test_anything_that_is_not_a_cursor_of_this_list_is_refused(token: str) -> None:
    with pytest.raises(paging.InvalidCursorError) as caught:
        paging.decode_cursor("runs", token)
    assert caught.value.status_code == 422 and caught.value.code == "invalid_cursor"


def test_a_cursor_is_refused_by_another_list() -> None:
    token = paging.encode_cursor("incidents", MOMENT, ROW)
    with pytest.raises(paging.InvalidCursorError, match="different list"):
        paging.decode_cursor("runs", token)


def test_the_seek_is_one_index_descent_however_deep(db_session: Any) -> None:
    """Page 51 rows after a position 2,500 rows deep: the plan reads a few rows of the index,
    not the 2,500 an offset would walk and discard."""
    from backend.tests.api.test_runs import _suite, _user

    suite = _suite(db_session, _user(db_session, "pager@example.com"), target={"table": "T"})
    now = datetime.now(UTC)
    db_session.add_all(
        Run(suite_id=suite.id, status="succeeded", created_at=now - timedelta(seconds=i))
        for i in range(3000)
    )
    db_session.flush()
    db_session.execute(text("ANALYZE runs"))
    deep = db_session.execute(
        select(Run.created_at, Run.id)
        .order_by(Run.created_at.desc(), Run.id.desc())
        .offset(2499)
        .limit(1)
    ).one()
    stmt = (
        select(Run)
        .where(paging.after(Run.created_at, Run.id, (deep[0], deep[1])))
        .order_by(Run.created_at.desc(), Run.id.desc())
        .limit(51)
    )
    compiled = stmt.compile(db_session.get_bind(), compile_kwargs={"literal_binds": True})
    plan = db_session.execute(text(f"EXPLAIN (ANALYZE, FORMAT JSON) {compiled}")).scalar_one()

    def nodes(node: dict[str, Any]) -> Any:
        yield node
        for child in node.get("Plans", []):
            yield from nodes(child)

    scans = [n for n in nodes(plan[0]["Plan"]) if n.get("Relation Name") == "runs"]
    assert scans, plan
    read = sum(
        (n["Actual Rows"] + n.get("Rows Removed by Filter", 0)) * n["Actual Loops"] for n in scans
    )
    assert read <= 200, plan
