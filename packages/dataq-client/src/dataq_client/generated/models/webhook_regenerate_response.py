from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.webhook_regenerate_response_auth_mode import WebhookRegenerateResponseAuthMode

T = TypeVar("T", bound="WebhookRegenerateResponse")


@_attrs_define
class WebhookRegenerateResponse:
    """A regenerated inbound-webhook credential.

    `value` is returned HERE AND NOWHERE ELSE — DataQ stores it in the secret store
    and no read endpoint returns it again, so an operator who does not copy it now
    must regenerate again. `grace_until` is the moment the PREVIOUS value stops being
    accepted; it is `null` when nothing was carried over, which means the old value
    is already dead (there was none, or the grace window is configured to zero).

        Attributes:
            auth_mode (WebhookRegenerateResponseAuthMode):
            grace_until (datetime.datetime | None):
            inbound_url (None | str):
            provider (str):
            secret_name (str):
            value (str):
    """

    auth_mode: WebhookRegenerateResponseAuthMode
    grace_until: datetime.datetime | None
    inbound_url: None | str
    provider: str
    secret_name: str
    value: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        auth_mode = self.auth_mode.value

        grace_until: None | str
        if isinstance(self.grace_until, datetime.datetime):
            grace_until = self.grace_until.isoformat()
        else:
            grace_until = self.grace_until

        inbound_url: None | str
        inbound_url = self.inbound_url

        provider = self.provider

        secret_name = self.secret_name

        value = self.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "auth_mode": auth_mode,
                "grace_until": grace_until,
                "inbound_url": inbound_url,
                "provider": provider,
                "secret_name": secret_name,
                "value": value,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        auth_mode = WebhookRegenerateResponseAuthMode(d.pop("auth_mode"))

        def _parse_grace_until(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                grace_until_type_0 = datetime.datetime.fromisoformat(data)

                return grace_until_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        grace_until = _parse_grace_until(d.pop("grace_until"))

        def _parse_inbound_url(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        inbound_url = _parse_inbound_url(d.pop("inbound_url"))

        provider = d.pop("provider")

        secret_name = d.pop("secret_name")

        value = d.pop("value")

        webhook_regenerate_response = cls(
            auth_mode=auth_mode,
            grace_until=grace_until,
            inbound_url=inbound_url,
            provider=provider,
            secret_name=secret_name,
            value=value,
        )

        webhook_regenerate_response.additional_properties = d
        return webhook_regenerate_response

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
