from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="AdminWebhookRead")


@_attrs_define
class AdminWebhookRead:
    """
    Attributes:
        auth (str):
        connection_names (list[str]):
        inbound_url (str):
        provider (str):
        signing_secret_name (None | str):
        token_configured (bool):
    """

    auth: str
    connection_names: list[str]
    inbound_url: str
    provider: str
    signing_secret_name: None | str
    token_configured: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        auth = self.auth

        connection_names = self.connection_names

        inbound_url = self.inbound_url

        provider = self.provider

        signing_secret_name: None | str
        signing_secret_name = self.signing_secret_name

        token_configured = self.token_configured

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "auth": auth,
                "connection_names": connection_names,
                "inbound_url": inbound_url,
                "provider": provider,
                "signing_secret_name": signing_secret_name,
                "token_configured": token_configured,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        auth = d.pop("auth")

        connection_names = cast(list[str], d.pop("connection_names"))

        inbound_url = d.pop("inbound_url")

        provider = d.pop("provider")

        def _parse_signing_secret_name(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        signing_secret_name = _parse_signing_secret_name(d.pop("signing_secret_name"))

        token_configured = d.pop("token_configured")

        admin_webhook_read = cls(
            auth=auth,
            connection_names=connection_names,
            inbound_url=inbound_url,
            provider=provider,
            signing_secret_name=signing_secret_name,
            token_configured=token_configured,
        )

        admin_webhook_read.additional_properties = d
        return admin_webhook_read

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
