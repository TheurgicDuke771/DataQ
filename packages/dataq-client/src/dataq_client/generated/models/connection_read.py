from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.connection_read_config import ConnectionReadConfig
    from ..models.connection_read_engine_capabilities_type_0 import (
        ConnectionReadEngineCapabilitiesType0,
    )
    from ..models.credential_health_read import CredentialHealthRead


T = TypeVar("T", bound="ConnectionRead")


@_attrs_define
class ConnectionRead:
    """
    Attributes:
        config (ConnectionReadConfig):
        created_by (None | UUID):
        env (str):
        has_secret (bool):
        id (UUID):
        name (str):
        type_ (str):
        consecutive_poll_failures (int | Unset):  Default: 0.
        consecutive_run_failures (int | Unset):  Default: 0.
        credential_expires_at (datetime.datetime | None | Unset):
        credential_expiry_checked_at (datetime.datetime | None | Unset):
        credential_health (CredentialHealthRead | None | Unset):
        engine_capabilities (ConnectionReadEngineCapabilitiesType0 | None | Unset):
        health_score (float | None | Unset):
        inventory_sync_failing_since (datetime.datetime | None | Unset):
        inventory_sync_last_attempted_at (datetime.datetime | None | Unset):
        inventory_sync_last_error (None | str | Unset):
        inventory_sync_last_table_count (int | None | Unset):
        inventory_sync_zero_since (datetime.datetime | None | Unset):
        last_poll_error (None | str | Unset):
        last_polled_at (datetime.datetime | None | Unset):
        last_run_at (datetime.datetime | None | Unset):
        last_run_error (None | str | Unset):
        refused_expectation_types (list[str] | Unset):
    """

    config: ConnectionReadConfig
    created_by: None | UUID
    env: str
    has_secret: bool
    id: UUID
    name: str
    type_: str
    consecutive_poll_failures: int | Unset = 0
    consecutive_run_failures: int | Unset = 0
    credential_expires_at: datetime.datetime | None | Unset = UNSET
    credential_expiry_checked_at: datetime.datetime | None | Unset = UNSET
    credential_health: CredentialHealthRead | None | Unset = UNSET
    engine_capabilities: ConnectionReadEngineCapabilitiesType0 | None | Unset = UNSET
    health_score: float | None | Unset = UNSET
    inventory_sync_failing_since: datetime.datetime | None | Unset = UNSET
    inventory_sync_last_attempted_at: datetime.datetime | None | Unset = UNSET
    inventory_sync_last_error: None | str | Unset = UNSET
    inventory_sync_last_table_count: int | None | Unset = UNSET
    inventory_sync_zero_since: datetime.datetime | None | Unset = UNSET
    last_poll_error: None | str | Unset = UNSET
    last_polled_at: datetime.datetime | None | Unset = UNSET
    last_run_at: datetime.datetime | None | Unset = UNSET
    last_run_error: None | str | Unset = UNSET
    refused_expectation_types: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.connection_read_engine_capabilities_type_0 import (
            ConnectionReadEngineCapabilitiesType0,
        )
        from ..models.credential_health_read import CredentialHealthRead

        config = self.config.to_dict()

        created_by: None | str
        if isinstance(self.created_by, UUID):
            created_by = str(self.created_by)
        else:
            created_by = self.created_by

        env = self.env

        has_secret = self.has_secret

        id = str(self.id)

        name = self.name

        type_ = self.type_

        consecutive_poll_failures = self.consecutive_poll_failures

        consecutive_run_failures = self.consecutive_run_failures

        credential_expires_at: None | str | Unset
        if isinstance(self.credential_expires_at, Unset):
            credential_expires_at = UNSET
        elif isinstance(self.credential_expires_at, datetime.datetime):
            credential_expires_at = self.credential_expires_at.isoformat()
        else:
            credential_expires_at = self.credential_expires_at

        credential_expiry_checked_at: None | str | Unset
        if isinstance(self.credential_expiry_checked_at, Unset):
            credential_expiry_checked_at = UNSET
        elif isinstance(self.credential_expiry_checked_at, datetime.datetime):
            credential_expiry_checked_at = self.credential_expiry_checked_at.isoformat()
        else:
            credential_expiry_checked_at = self.credential_expiry_checked_at

        credential_health: dict[str, Any] | None | Unset
        if isinstance(self.credential_health, Unset):
            credential_health = UNSET
        elif isinstance(self.credential_health, CredentialHealthRead):
            credential_health = self.credential_health.to_dict()
        else:
            credential_health = self.credential_health

        engine_capabilities: dict[str, Any] | None | Unset
        if isinstance(self.engine_capabilities, Unset):
            engine_capabilities = UNSET
        elif isinstance(self.engine_capabilities, ConnectionReadEngineCapabilitiesType0):
            engine_capabilities = self.engine_capabilities.to_dict()
        else:
            engine_capabilities = self.engine_capabilities

        health_score: float | None | Unset
        if isinstance(self.health_score, Unset):
            health_score = UNSET
        else:
            health_score = self.health_score

        inventory_sync_failing_since: None | str | Unset
        if isinstance(self.inventory_sync_failing_since, Unset):
            inventory_sync_failing_since = UNSET
        elif isinstance(self.inventory_sync_failing_since, datetime.datetime):
            inventory_sync_failing_since = self.inventory_sync_failing_since.isoformat()
        else:
            inventory_sync_failing_since = self.inventory_sync_failing_since

        inventory_sync_last_attempted_at: None | str | Unset
        if isinstance(self.inventory_sync_last_attempted_at, Unset):
            inventory_sync_last_attempted_at = UNSET
        elif isinstance(self.inventory_sync_last_attempted_at, datetime.datetime):
            inventory_sync_last_attempted_at = self.inventory_sync_last_attempted_at.isoformat()
        else:
            inventory_sync_last_attempted_at = self.inventory_sync_last_attempted_at

        inventory_sync_last_error: None | str | Unset
        if isinstance(self.inventory_sync_last_error, Unset):
            inventory_sync_last_error = UNSET
        else:
            inventory_sync_last_error = self.inventory_sync_last_error

        inventory_sync_last_table_count: int | None | Unset
        if isinstance(self.inventory_sync_last_table_count, Unset):
            inventory_sync_last_table_count = UNSET
        else:
            inventory_sync_last_table_count = self.inventory_sync_last_table_count

        inventory_sync_zero_since: None | str | Unset
        if isinstance(self.inventory_sync_zero_since, Unset):
            inventory_sync_zero_since = UNSET
        elif isinstance(self.inventory_sync_zero_since, datetime.datetime):
            inventory_sync_zero_since = self.inventory_sync_zero_since.isoformat()
        else:
            inventory_sync_zero_since = self.inventory_sync_zero_since

        last_poll_error: None | str | Unset
        if isinstance(self.last_poll_error, Unset):
            last_poll_error = UNSET
        else:
            last_poll_error = self.last_poll_error

        last_polled_at: None | str | Unset
        if isinstance(self.last_polled_at, Unset):
            last_polled_at = UNSET
        elif isinstance(self.last_polled_at, datetime.datetime):
            last_polled_at = self.last_polled_at.isoformat()
        else:
            last_polled_at = self.last_polled_at

        last_run_at: None | str | Unset
        if isinstance(self.last_run_at, Unset):
            last_run_at = UNSET
        elif isinstance(self.last_run_at, datetime.datetime):
            last_run_at = self.last_run_at.isoformat()
        else:
            last_run_at = self.last_run_at

        last_run_error: None | str | Unset
        if isinstance(self.last_run_error, Unset):
            last_run_error = UNSET
        else:
            last_run_error = self.last_run_error

        refused_expectation_types: list[str] | Unset = UNSET
        if not isinstance(self.refused_expectation_types, Unset):
            refused_expectation_types = self.refused_expectation_types

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "config": config,
                "created_by": created_by,
                "env": env,
                "has_secret": has_secret,
                "id": id,
                "name": name,
                "type": type_,
            }
        )
        if consecutive_poll_failures is not UNSET:
            field_dict["consecutive_poll_failures"] = consecutive_poll_failures
        if consecutive_run_failures is not UNSET:
            field_dict["consecutive_run_failures"] = consecutive_run_failures
        if credential_expires_at is not UNSET:
            field_dict["credential_expires_at"] = credential_expires_at
        if credential_expiry_checked_at is not UNSET:
            field_dict["credential_expiry_checked_at"] = credential_expiry_checked_at
        if credential_health is not UNSET:
            field_dict["credential_health"] = credential_health
        if engine_capabilities is not UNSET:
            field_dict["engine_capabilities"] = engine_capabilities
        if health_score is not UNSET:
            field_dict["health_score"] = health_score
        if inventory_sync_failing_since is not UNSET:
            field_dict["inventory_sync_failing_since"] = inventory_sync_failing_since
        if inventory_sync_last_attempted_at is not UNSET:
            field_dict["inventory_sync_last_attempted_at"] = inventory_sync_last_attempted_at
        if inventory_sync_last_error is not UNSET:
            field_dict["inventory_sync_last_error"] = inventory_sync_last_error
        if inventory_sync_last_table_count is not UNSET:
            field_dict["inventory_sync_last_table_count"] = inventory_sync_last_table_count
        if inventory_sync_zero_since is not UNSET:
            field_dict["inventory_sync_zero_since"] = inventory_sync_zero_since
        if last_poll_error is not UNSET:
            field_dict["last_poll_error"] = last_poll_error
        if last_polled_at is not UNSET:
            field_dict["last_polled_at"] = last_polled_at
        if last_run_at is not UNSET:
            field_dict["last_run_at"] = last_run_at
        if last_run_error is not UNSET:
            field_dict["last_run_error"] = last_run_error
        if refused_expectation_types is not UNSET:
            field_dict["refused_expectation_types"] = refused_expectation_types

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.connection_read_config import ConnectionReadConfig
        from ..models.connection_read_engine_capabilities_type_0 import (
            ConnectionReadEngineCapabilitiesType0,
        )
        from ..models.credential_health_read import CredentialHealthRead

        d = dict(src_dict)
        config = ConnectionReadConfig.from_dict(d.pop("config"))

        def _parse_created_by(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                created_by_type_0 = UUID(data)

                return created_by_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | UUID, data)

        created_by = _parse_created_by(d.pop("created_by"))

        env = d.pop("env")

        has_secret = d.pop("has_secret")

        id = UUID(d.pop("id"))

        name = d.pop("name")

        type_ = d.pop("type")

        consecutive_poll_failures = d.pop("consecutive_poll_failures", UNSET)

        consecutive_run_failures = d.pop("consecutive_run_failures", UNSET)

        def _parse_credential_expires_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                credential_expires_at_type_0 = datetime.datetime.fromisoformat(data)

                return credential_expires_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        credential_expires_at = _parse_credential_expires_at(d.pop("credential_expires_at", UNSET))

        def _parse_credential_expiry_checked_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                credential_expiry_checked_at_type_0 = datetime.datetime.fromisoformat(data)

                return credential_expiry_checked_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        credential_expiry_checked_at = _parse_credential_expiry_checked_at(
            d.pop("credential_expiry_checked_at", UNSET)
        )

        def _parse_credential_health(data: object) -> CredentialHealthRead | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                credential_health_type_0 = CredentialHealthRead.from_dict(data)

                return credential_health_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(CredentialHealthRead | None | Unset, data)

        credential_health = _parse_credential_health(d.pop("credential_health", UNSET))

        def _parse_engine_capabilities(
            data: object,
        ) -> ConnectionReadEngineCapabilitiesType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                engine_capabilities_type_0 = ConnectionReadEngineCapabilitiesType0.from_dict(data)

                return engine_capabilities_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ConnectionReadEngineCapabilitiesType0 | None | Unset, data)

        engine_capabilities = _parse_engine_capabilities(d.pop("engine_capabilities", UNSET))

        def _parse_health_score(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        health_score = _parse_health_score(d.pop("health_score", UNSET))

        def _parse_inventory_sync_failing_since(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                inventory_sync_failing_since_type_0 = datetime.datetime.fromisoformat(data)

                return inventory_sync_failing_since_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        inventory_sync_failing_since = _parse_inventory_sync_failing_since(
            d.pop("inventory_sync_failing_since", UNSET)
        )

        def _parse_inventory_sync_last_attempted_at(
            data: object,
        ) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                inventory_sync_last_attempted_at_type_0 = datetime.datetime.fromisoformat(data)

                return inventory_sync_last_attempted_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        inventory_sync_last_attempted_at = _parse_inventory_sync_last_attempted_at(
            d.pop("inventory_sync_last_attempted_at", UNSET)
        )

        def _parse_inventory_sync_last_error(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        inventory_sync_last_error = _parse_inventory_sync_last_error(
            d.pop("inventory_sync_last_error", UNSET)
        )

        def _parse_inventory_sync_last_table_count(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        inventory_sync_last_table_count = _parse_inventory_sync_last_table_count(
            d.pop("inventory_sync_last_table_count", UNSET)
        )

        def _parse_inventory_sync_zero_since(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                inventory_sync_zero_since_type_0 = datetime.datetime.fromisoformat(data)

                return inventory_sync_zero_since_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        inventory_sync_zero_since = _parse_inventory_sync_zero_since(
            d.pop("inventory_sync_zero_since", UNSET)
        )

        def _parse_last_poll_error(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        last_poll_error = _parse_last_poll_error(d.pop("last_poll_error", UNSET))

        def _parse_last_polled_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_polled_at_type_0 = datetime.datetime.fromisoformat(data)

                return last_polled_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        last_polled_at = _parse_last_polled_at(d.pop("last_polled_at", UNSET))

        def _parse_last_run_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_run_at_type_0 = datetime.datetime.fromisoformat(data)

                return last_run_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        last_run_at = _parse_last_run_at(d.pop("last_run_at", UNSET))

        def _parse_last_run_error(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        last_run_error = _parse_last_run_error(d.pop("last_run_error", UNSET))

        refused_expectation_types = cast(list[str], d.pop("refused_expectation_types", UNSET))

        connection_read = cls(
            config=config,
            created_by=created_by,
            env=env,
            has_secret=has_secret,
            id=id,
            name=name,
            type_=type_,
            consecutive_poll_failures=consecutive_poll_failures,
            consecutive_run_failures=consecutive_run_failures,
            credential_expires_at=credential_expires_at,
            credential_expiry_checked_at=credential_expiry_checked_at,
            credential_health=credential_health,
            engine_capabilities=engine_capabilities,
            health_score=health_score,
            inventory_sync_failing_since=inventory_sync_failing_since,
            inventory_sync_last_attempted_at=inventory_sync_last_attempted_at,
            inventory_sync_last_error=inventory_sync_last_error,
            inventory_sync_last_table_count=inventory_sync_last_table_count,
            inventory_sync_zero_since=inventory_sync_zero_since,
            last_poll_error=last_poll_error,
            last_polled_at=last_polled_at,
            last_run_at=last_run_at,
            last_run_error=last_run_error,
            refused_expectation_types=refused_expectation_types,
        )

        connection_read.additional_properties = d
        return connection_read

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
