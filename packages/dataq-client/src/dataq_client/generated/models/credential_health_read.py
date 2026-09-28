from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.credential_health_read_status import CredentialHealthReadStatus

T = TypeVar("T", bound="CredentialHealthRead")


@_attrs_define
class CredentialHealthRead:
    """A datasource connection's stored-credential health, derived from real use (#1697).

    `status="unknown"` — never `healthy` — means the credential has not been used since
    the signal shipped: nothing has run, dry-run, profiled or tested against it, so DataQ
    has observed nothing. `failing` means the datasource rejected the credential on its
    last use; `last_error` is then the classified, secret-free reason.

    Only credential REJECTIONS move this. A missing grant, an unreachable host and a bad
    table name leave it untouched, because none of them says the credential is dead.

    Orchestration connections carry `null` here — their health is the poll signal above.

        Attributes:
            consecutive_auth_failures (int):
            last_auth_failure_at (datetime.datetime | None):
            last_auth_success_at (datetime.datetime | None):
            last_error (None | str):
            status (CredentialHealthReadStatus):
    """

    consecutive_auth_failures: int
    last_auth_failure_at: datetime.datetime | None
    last_auth_success_at: datetime.datetime | None
    last_error: None | str
    status: CredentialHealthReadStatus
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        consecutive_auth_failures = self.consecutive_auth_failures

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

        status = self.status.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "consecutive_auth_failures": consecutive_auth_failures,
                "last_auth_failure_at": last_auth_failure_at,
                "last_auth_success_at": last_auth_success_at,
                "last_error": last_error,
                "status": status,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        consecutive_auth_failures = d.pop("consecutive_auth_failures")

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

        status = CredentialHealthReadStatus(d.pop("status"))

        credential_health_read = cls(
            consecutive_auth_failures=consecutive_auth_failures,
            last_auth_failure_at=last_auth_failure_at,
            last_auth_success_at=last_auth_success_at,
            last_error=last_error,
            status=status,
        )

        credential_health_read.additional_properties = d
        return credential_health_read

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
