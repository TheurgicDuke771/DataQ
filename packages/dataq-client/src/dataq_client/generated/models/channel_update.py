from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.channel_update_payload_template_type_0 import ChannelUpdatePayloadTemplateType0


T = TypeVar("T", bound="ChannelUpdate")


@_attrs_define
class ChannelUpdate:
    """
    Attributes:
        auth_header_name (None | str | Unset): Set the auth header name; empty string clears it
        auth_header_value (None | str | Unset):
        clear_payload_template (bool | Unset): Remove the template (an empty object is a legitimate template, so
            clearing needs its own flag rather than overloading an empty payload_template) Default: False.
        email_recipients (None | str | Unset):
        name (None | str | Unset):
        payload_template (ChannelUpdatePayloadTemplateType0 | None | Unset): Set/replace the payload template
        regenerate_hmac_secret (bool | Unset): Mint a new HMAC signing key, invalidating the old one Default: False.
        webhook (None | str | Unset):
        webhook_url (None | str | Unset):
    """

    auth_header_name: None | str | Unset = UNSET
    auth_header_value: None | str | Unset = UNSET
    clear_payload_template: bool | Unset = False
    email_recipients: None | str | Unset = UNSET
    name: None | str | Unset = UNSET
    payload_template: ChannelUpdatePayloadTemplateType0 | None | Unset = UNSET
    regenerate_hmac_secret: bool | Unset = False
    webhook: None | str | Unset = UNSET
    webhook_url: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.channel_update_payload_template_type_0 import (
            ChannelUpdatePayloadTemplateType0,
        )

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

        clear_payload_template = self.clear_payload_template

        email_recipients: None | str | Unset
        if isinstance(self.email_recipients, Unset):
            email_recipients = UNSET
        else:
            email_recipients = self.email_recipients

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        payload_template: dict[str, Any] | None | Unset
        if isinstance(self.payload_template, Unset):
            payload_template = UNSET
        elif isinstance(self.payload_template, ChannelUpdatePayloadTemplateType0):
            payload_template = self.payload_template.to_dict()
        else:
            payload_template = self.payload_template

        regenerate_hmac_secret = self.regenerate_hmac_secret

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

        field_dict.update({})
        if auth_header_name is not UNSET:
            field_dict["auth_header_name"] = auth_header_name
        if auth_header_value is not UNSET:
            field_dict["auth_header_value"] = auth_header_value
        if clear_payload_template is not UNSET:
            field_dict["clear_payload_template"] = clear_payload_template
        if email_recipients is not UNSET:
            field_dict["email_recipients"] = email_recipients
        if name is not UNSET:
            field_dict["name"] = name
        if payload_template is not UNSET:
            field_dict["payload_template"] = payload_template
        if regenerate_hmac_secret is not UNSET:
            field_dict["regenerate_hmac_secret"] = regenerate_hmac_secret
        if webhook is not UNSET:
            field_dict["webhook"] = webhook
        if webhook_url is not UNSET:
            field_dict["webhook_url"] = webhook_url

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.channel_update_payload_template_type_0 import (
            ChannelUpdatePayloadTemplateType0,
        )

        d = dict(src_dict)

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

        clear_payload_template = d.pop("clear_payload_template", UNSET)

        def _parse_email_recipients(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        email_recipients = _parse_email_recipients(d.pop("email_recipients", UNSET))

        def _parse_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name = _parse_name(d.pop("name", UNSET))

        def _parse_payload_template(
            data: object,
        ) -> ChannelUpdatePayloadTemplateType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                payload_template_type_0 = ChannelUpdatePayloadTemplateType0.from_dict(data)

                return payload_template_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ChannelUpdatePayloadTemplateType0 | None | Unset, data)

        payload_template = _parse_payload_template(d.pop("payload_template", UNSET))

        regenerate_hmac_secret = d.pop("regenerate_hmac_secret", UNSET)

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

        channel_update = cls(
            auth_header_name=auth_header_name,
            auth_header_value=auth_header_value,
            clear_payload_template=clear_payload_template,
            email_recipients=email_recipients,
            name=name,
            payload_template=payload_template,
            regenerate_hmac_secret=regenerate_hmac_secret,
            webhook=webhook,
            webhook_url=webhook_url,
        )

        return channel_update
