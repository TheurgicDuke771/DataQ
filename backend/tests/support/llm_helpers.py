"""Shared fixtures-in-functions for the LLM test family (ADR 0042)."""

from __future__ import annotations

import uuid
from typing import Any

from backend.app.db.models import Connection, Suite, User
from backend.app.services import llm_service
from backend.tests.support.fake_secret_store import FakeSecretStore


def enable_llm(
    db_session: Any, actor: User, store: FakeSecretStore | None = None
) -> FakeSecretStore:
    store = store or FakeSecretStore()
    llm_service.save_settings(
        db_session,
        draft=llm_service.LlmSettingsDraft(
            provider="openai_compatible", model="m", base_url="http://x/v1", api_key="k"
        ),
        actor=actor,
        secret_store=store,
    )
    db_session.commit()
    return store


def make_sql_suite(
    db_session: Any,
    owner: User,
    *,
    conn_type: str = "snowflake",
    target: dict[str, Any] | None = None,
    column_policy: dict[str, Any] | None = None,
) -> Suite:
    connection = Connection(
        id=uuid.uuid4(),
        name=f"c-{uuid.uuid4().hex[:6]}",
        type=conn_type,
        env="dev",
        config={},
        created_by=owner.id,
    )
    db_session.add(connection)
    db_session.flush()
    suite = Suite(
        name=f"s-{uuid.uuid4().hex[:6]}",
        connection_id=connection.id,
        created_by=owner.id,
        target=target if target is not None else {"table": "ORDERS", "schema": "RETAIL"},
        column_policy=column_policy,
    )
    db_session.add(suite)
    db_session.commit()
    return suite
