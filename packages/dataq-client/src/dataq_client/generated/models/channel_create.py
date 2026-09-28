from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from typing_extensions import Self

from ..models.channel_create_type import ChannelCreateType
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.channel_create_payload_template_type_0 import ChannelCreatePayloadTemplateType0


T = TypeVar("T", bound="ChannelCreate")


@_attrs_define
class ChannelCreate:
    """
    Attributes:
        name (str):
        type_ (ChannelCreateType):
        auth_header_name (None | str | Unset): An extra header some receivers need beside the HMAC signature
        auth_header_value (None | str | Unset): The header value; write-only
        email_recipients (None | str | Unset): Comma-separated addresses
        payload_template (ChannelCreatePayloadTemplateType0 | None | Unset): Reshapes the generic webhook payload for a
            specific receiver (PagerDuty/Opsgenie/ServiceNow/Jira). {{field.path}} placeholders resolve against the generic
            payload by key lookup only. WARNING: this is stored as plain JSON, not a SecretStore-backed credential — never
            paste a real secret in as a literal value (a routing/integration key, an API token). Put any credential in
            auth_header_value instead, which is write-only and encrypted at rest; readable only by workspace admins.
        webhook (None | str | Unset): Teams/Slack webhook URL; write-only
        webhook_url (None | str | Unset): Generic webhook destination URL (https, non-internal)
    """

    name: str
    type_: ChannelCreateType
    auth_header_name: None | str | Unset = UNSET
    auth_header_value: None | str | Unset = UNSET
    email_recipients: None | str | Unset = UNSET
    payload_template: ChannelCreatePayloadTemplateType0 | None | Unset = UNSET
    webhook: None | str | Unset = UNSET
    webhook_url: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.channel_create_payload_template_type_0 import (
            ChannelCreatePayloadTemplateType0,
        )

        name = self.name

        type_ = self.type_.value

        auth_header_name: None | str | Unset
        if isinstance(self.auth_header_name, Unset):
            auth_header_name = UNSET
        else:
            auth_header_name = self.auth_header_name

        auth_header_value: None | str | Unset
        if isinstance(self.auth_header_value, Unset):
            auth_header_value = UNSET
        else:
            auth_header_value = self.auth_header_value

        email_recipients: None | str | Unset
        if isinstance(self.email_recipients, Unset):
            email_recipients = UNSET
        else:
            email_recipients = self.email_recipients

        payload_template: dict[str, Any] | None | Unset
        if isinstance(self.payload_template, Unset):
            payload_template = UNSET
        elif isinstance(self.payload_template, ChannelCreatePayloadTemplateType0):
            payload_template = self.payload_template.to_dict()
        else:
            payload_template = self.payload_template

        webhook: None | str | Unset
        if isinstance(self.webhook, Unset):
            webhook = UNSET
        else:
            webhook = self.webhook

        webhook_url: None | str | Unset
        if isinstance(self.webhook_url, Unset):
            webhook_url = UNSET
        else:
            webhook_url = self.webhook_url

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "name": name,
                "type": type_,
            }
        )
        if auth_header_name is not UNSET:
            field_dict["auth_header_name"] = auth_header_name
        if auth_header_value is not UNSET:
            field_dict["auth_header_value"] = auth_header_value
        if email_recipients is not UNSET:
            field_dict["email_recipients"] = email_recipients
        if payload_template is not UNSET:
            field_dict["payload_template"] = payload_template
        if webhook is not UNSET:
            field_dict["webhook"] = webhook
        if webhook_url is not UNSET:
            field_dict["webhook_url"] = webhook_url

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.channel_create_payload_template_type_0 import (
            ChannelCreatePayloadTemplateType0,
        )

        d = dict(src_dict)
        name = d.pop("name")

        type_ = ChannelCreateType(d.pop("type"))

        def _parse_auth_header_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        auth_header_name = _parse_auth_header_name(d.pop("auth_header_name", UNSET))

        def _parse_auth_header_value(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        auth_header_value = _parse_auth_header_value(d.pop("auth_header_value", UNSET))

        def _parse_email_recipients(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        email_recipients = _parse_email_recipients(d.pop("email_recipients", UNSET))

        def _parse_payload_template(
            data: object,
        ) -> ChannelCreatePayloadTemplateType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                payload_template_type_0 = ChannelCreatePayloadTemplateType0.from_dict(data)

                return payload_template_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ChannelCreatePayloadTemplateType0 | None | Unset, data)

        payload_template = _parse_payload_template(d.pop("payload_template", UNSET))

        def _parse_webhook(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        webhook = _parse_webhook(d.pop("webhook", UNSET))

        def _parse_webhook_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        webhook_url = _parse_webhook_url(d.pop("webhook_url", UNSET))

        channel_create = cls(
            name=name,
            type_=type_,
            auth_header_name=auth_header_name,
            auth_header_value=auth_header_value,
            email_recipients=email_recipients,
            payload_template=payload_template,
            webhook=webhook,
            webhook_url=webhook_url,
        )

        return channel_create
