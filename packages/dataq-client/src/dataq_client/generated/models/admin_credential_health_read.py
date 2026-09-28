from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.admin_credential_health_read_status import AdminCredentialHealthReadStatus

T = TypeVar("T", bound="AdminCredentialHealthRead")


@_attrs_define
class AdminCredentialHealthRead:
    """One datasource connection's stored-credential health (#1697).

    `status="unknown"` — never `healthy` — means the credential has not been used
    since the signal shipped, so nothing has been observed about it. `failing` means
    the datasource rejected it on its last use, and `last_error` is then the
    classified, secret-free reason. Only credential REJECTIONS move this: a missing
    grant, an unreachable host and a bad table name leave it untouched.

    Derived from real work (runs, dry-runs, profiles, connection tests), not from a
    periodic probe — so a connection nothing uses stays `unknown` indefinitely, which
    is the honest answer rather than a reassuring one.

        Attributes:
            connection_id (UUID):
            consecutive_auth_failures (int):
            env (str):
            last_auth_failure_at (datetime.datetime | None):
            last_auth_success_at (datetime.datetime | None):
            last_error (None | str):
            name (str):
            status (AdminCredentialHealthReadStatus):
            type_ (str):
    """

    connection_id: UUID
    consecutive_auth_failures: int
    env: str
    last_auth_failure_at: datetime.datetime | None
    last_auth_success_at: datetime.datetime | None
    last_error: None | str
    name: str
    status: AdminCredentialHealthReadStatus
    type_: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        connection_id = str(self.connection_id)

        consecutive_auth_failures = self.consecutive_auth_failures

        env = self.env

        last_auth_failure_at: None | str
        if isinstance(self.last_auth_failure_at, datetime.datetime):
            last_auth_failure_at = self.last_auth_failure_at.isoformat()
        else:
            last_auth_failure_at = self.last_auth_failure_at

        last_auth_success_at: None | str
        if isinstance(self.last_auth_success_at, datetime.datetime):
            last_auth_success_at = self.last_auth_success_at.isoformat()
        else:
            last_auth_success_at = self.last_auth_success_at

        last_error: None | str
        last_error = self.last_error

        name = self.name

        status = self.status.value

        type_ = self.type_

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "connection_id": connection_id,
                "consecutive_auth_failures": consecutive_auth_failures,
                "env": env,
                "last_auth_failure_at": last_auth_failure_at,
                "last_auth_success_at": last_auth_success_at,
                "last_error": last_error,
                "name": name,
                "status": status,
                "type": type_,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        connection_id = UUID(d.pop("connection_id"))

        consecutive_auth_failures = d.pop("consecutive_auth_failures")

        env = d.pop("env")

        def _parse_last_auth_failure_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_auth_failure_at_type_0 = datetime.datetime.fromisoformat(data)

                return last_auth_failure_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        last_auth_failure_at = _parse_last_auth_failure_at(d.pop("last_auth_failure_at"))

        def _parse_last_auth_success_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_auth_success_at_type_0 = datetime.datetime.fromisoformat(data)

                return last_auth_success_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        last_auth_success_at = _parse_last_auth_success_at(d.pop("last_auth_success_at"))

        def _parse_last_error(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        last_error = _parse_last_error(d.pop("last_error"))

        name = d.pop("name")

        status = AdminCredentialHealthReadStatus(d.pop("status"))

        type_ = d.pop("type")

        admin_credential_health_read = cls(
            connection_id=connection_id,
            consecutive_auth_failures=consecutive_auth_failures,
            env=env,
            last_auth_failure_at=last_auth_failure_at,
            last_auth_success_at=last_auth_success_at,
            last_error=last_error,
            name=name,
            status=status,
            type_=type_,
        )

        admin_credential_health_read.additional_properties = d
        return admin_credential_health_read

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
