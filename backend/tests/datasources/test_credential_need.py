"""One answer to "does this connection need a stored secret?", from the adapter (#2724)."""

from __future__ import annotations

from typing import Any

import pytest

from backend.app.datasources.registry import (
    _ADAPTERS,
    authenticates_without_secret,
    credential_not_applicable,
    get_connection_adapter,
    missing_credential,
)
from backend.app.services.credential_health import is_datasource
from backend.tests.support.credential_configs import (
    CREDENTIAL_CONFIGS,
    EXTRA_CREDENTIAL_CONFIGS,
    NO_CREDENTIAL_CONFIGS,
)

ALL_CONFIGS = NO_CREDENTIAL_CONFIGS + CREDENTIAL_CONFIGS + EXTRA_CREDENTIAL_CONFIGS


def test_every_optional_secret_datasource_is_walked_and_answers_per_config() -> None:
    optional = {
        conn_type
        for conn_type, adapter in _ADAPTERS.items()
        if getattr(adapter, "secret_optional", False) and is_datasource(conn_type)
    }
    assert optional == {conn_type for conn_type, _ in ALL_CONFIGS}
    for conn_type, config in ALL_CONFIGS:
        model = get_connection_adapter(conn_type).validate_config(dict(config))
        assert callable(getattr(model, "requires_secret", None)), conn_type


@pytest.mark.parametrize(("conn_type", "config"), NO_CREDENTIAL_CONFIGS)
def test_a_config_that_needs_no_credential(conn_type: str, config: dict[str, Any]) -> None:
    assert authenticates_without_secret(conn_type, config)
    assert not missing_credential(conn_type, config, None)
    assert credential_not_applicable(conn_type, config, None)
    # A secret left behind by an edit is unused, so still nothing to observe.
    assert credential_not_applicable(conn_type, config, "c-old")


@pytest.mark.parametrize(("conn_type", "config"), CREDENTIAL_CONFIGS)
def test_a_config_that_sends_the_stored_secret(conn_type: str, config: dict[str, Any]) -> None:
    assert not authenticates_without_secret(conn_type, config)
    assert missing_credential(conn_type, config, None)
    assert not missing_credential(conn_type, config, "c-x")
    assert not credential_not_applicable(conn_type, config, None)
    assert not credential_not_applicable(conn_type, config, "c-x")


@pytest.mark.parametrize(("conn_type", "config"), EXTRA_CREDENTIAL_CONFIGS)
def test_a_config_with_only_an_extra_credential(conn_type: str, config: dict[str, Any]) -> None:
    # No `secret_ref` is needed, so none is missing, but the catalog password is a credential.
    assert authenticates_without_secret(conn_type, config)
    assert not missing_credential(conn_type, config, None)
    assert not credential_not_applicable(conn_type, config, None)
