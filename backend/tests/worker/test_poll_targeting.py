"""Targeted poll (#492): provider/resource_name narrow the sweep.

The alert-triggered poll-now path must poll only the alerting orchestrator —
an alert storm must not amplify into full sweeps of every connection.
"""

import uuid
from typing import Any

import pytest

from backend.app.core.secrets import SecretNotFoundError
from backend.app.db.models import Connection, User
from backend.app.worker.tasks import _poll_orchestration_runs


class _Store:
    def get(self, name: str) -> str:
        raise SecretNotFoundError(name)  # short-circuits before any network

    def set(self, name: str, value: str) -> None:  # pragma: no cover - unused
        pass

    def delete(self, name: str) -> None:
        pass


def _seed(db_session: Any) -> None:
    user = User(aad_object_id=uuid.uuid4().hex, email="poll@example.com")
    db_session.add(user)
    db_session.flush()
    # One orchestrator connection per (type, env) — spread across envs.
    for name, type_, env, cfg in (
        ("adf-a", "adf", "dev", {"factory_name": "factory-a"}),
        ("adf-b", "adf", "qa", {"factory_name": "factory-b"}),
        ("af-1", "airflow", "dev", {"base_url": "https://af.example"}),
    ):
        db_session.add(
            Connection(
                name=name,
                type=type_,
                env=env,
                config=cfg,
                created_by=user.id,
                secret_ref=f"secret-{name}",
            )
        )
    db_session.commit()


@pytest.mark.parametrize(
    ("provider", "resource_name", "expected_attempted"),
    [
        (None, None, 3),  # beat path: full sweep
        ("adf", None, 2),  # provider-targeted
        ("adf", "factory-a", 1),  # fully targeted
        ("adf", "factory-nope", 0),  # named factory matches nothing
    ],
)
def test_poll_targeting_narrows_connections(
    db_session: Any, provider: str | None, resource_name: str | None, expected_attempted: int
) -> None:
    _seed(db_session)
    summary = _poll_orchestration_runs(
        db_session,
        secret_store=_Store(),
        provider=provider,
        resource_name=resource_name,
    )
    # Every attempted connection errors at the secret read (no network) — so
    # `errors` counts exactly the connections the targeting let through.
    assert summary["errors"] == expected_attempted
    assert summary["connections"] == 0
