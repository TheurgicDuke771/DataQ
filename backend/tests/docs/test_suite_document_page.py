"""The Suite document reference page's examples are run, not trusted (#1688)."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.db.models import Connection, User
from backend.app.db.session import get_db
from backend.app.main import app

PAGE = Path(__file__).resolve().parents[3] / "docs/site/reference/suite-document.md"


def _blocks(language: str) -> list[str]:
    return re.findall(rf"```{language}\n(.*?)```", PAGE.read_text(), flags=re.DOTALL)


@pytest.fixture
def client(db_session: Any) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def connection_id(db_session: Any) -> str:
    owner = User(aad_object_id=uuid.uuid4().hex, email="docs@ex.com")
    db_session.add(owner)
    db_session.flush()
    conn = Connection(
        name="docs",
        type="snowflake",
        env="dev",
        config={"account": "ab12345.eu-west-1"},
        secret_ref="kv-x",
        created_by=owner.id,
    )
    db_session.add(conn)
    db_session.commit()
    return str(conn.id)


def test_the_page_example_validates_and_imports(client: TestClient, connection_id: str) -> None:
    (example,) = _blocks("yaml")
    body = {"connection_id": connection_id, "document_yaml": example}

    validated = client.post("/api/v1/suites/validate", json=body)
    imported = client.post("/api/v1/suites/import", json=body)

    assert validated.json() == {"valid": True, "check_count": 3, "problems": []}
    assert imported.status_code == 201, imported.text


def test_the_page_problem_output_is_what_the_server_returns(
    client: TestClient, connection_id: str
) -> None:
    (example,) = _blocks("yaml")
    (shown,) = _blocks("json")
    # The page's sample output is for the example with its second check's bands inverted.
    broken = example.replace(
        "warn_threshold: 0.5\n    fail_threshold: 2", "warn_threshold: 2\n    fail_threshold: 0.5"
    )
    assert broken != example

    resp = client.post(
        "/api/v1/suites/validate", json={"connection_id": connection_id, "document_yaml": broken}
    )

    assert resp.json() == json.loads(shown)
