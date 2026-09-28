from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.channel_read_payload_template_type_0 import ChannelReadPayloadTemplateType0


T = TypeVar("T", bound="ChannelRead")


@_attrs_define
class ChannelRead:
    """A channel's shape as any authenticated caller may see it. The Teams/Slack
    webhook and the generic webhook's HMAC signing key are credentials and
    never returned, only whether one is set — `webhook_url` is the exception:
    for a generic webhook it is the destination, not the credential (the HMAC
    signature is), so it is safe to echo back for admin visibility.

    `payload_template` is a partial exception: it isn't itself a SecretStore-
    backed credential column, but an admin authoring one (e.g. a PagerDuty
    Events-API body) commonly has nowhere else to put that receiver's static
    routing/integration key than as a literal in the template JSON — which
    functions as a credential even though the field's storage doesn't treat
    it as one (#1663 review). It's therefore only ever included for an
    Admin caller; every other authenticated user gets `has_payload_template`
    instead, the same presence-only shape already used for genuine secrets.

        Attributes:
            email_recipients (None | str):
            has_webhook (bool):
            id (UUID):
            name (str):
            type_ (str):
            auth_header_name (None | str | Unset):
            has_auth_header (bool | Unset):  Default: False.
            has_hmac_secret (bool | Unset):  Default: False.
            has_payload_template (bool | Unset):  Default: False.
            hmac_secret (None | str | Unset):
            payload_template (ChannelReadPayloadTemplateType0 | None | Unset):
            webhook_url (None | str | Unset):
    """

    email_recipients: None | str
    has_webhook: bool
    id: UUID
    name: str
    type_: str
    auth_header_name: None | str | Unset = UNSET
    has_auth_header: bool | Unset = False
    has_hmac_secret: bool | Unset = False
    has_payload_template: bool | Unset = False
    hmac_secret: None | str | Unset = UNSET
    payload_template: ChannelReadPayloadTemplateType0 | None | Unset = UNSET
    webhook_url: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.channel_read_payload_template_type_0 import (
            ChannelReadPayloadTemplateType0,
        )

        email_recipients: None | str
        email_recipients = self.email_recipients

        has_webhook = self.has_webhook

        id = str(self.id)

        name = self.name

        type_ = self.type_

        auth_header_name: None | str | Unset
        if isinstance(self.auth_header_name, Unset):
            auth_header_name = UNSET
        else:
            auth_header_name = self.auth_header_name

        has_auth_header = self.has_auth_header

        has_hmac_secret = self.has_hmac_secret

        has_payload_template = self.has_payload_template

        hmac_secret: None | str | Unset
        if isinstance(self.hmac_secret, Unset):
            hmac_secret = UNSET
        else:
            hmac_secret = self.hmac_secret

        payload_template: dict[str, Any] | None | Unset
        if isinstance(self.payload_template, Unset):
            payload_template = UNSET
        elif isinstance(self.payload_template, ChannelReadPayloadTemplateType0):
            payload_template = self.payload_template.to_dict()
        else:
            payload_template = self.payload_template

        webhook_url: None | str | Unset
        if isinstance(self.webhook_url, Unset):
            webhook_url = UNSET
        else:
            webhook_url = self.webhook_url

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "email_recipients": email_recipients,
                "has_webhook": has_webhook,
                "id": id,
                "name": name,
                "type": type_,
            }
        )
        if auth_header_name is not UNSET:
            field_dict["auth_header_name"] = auth_header_name
        if has_auth_header is not UNSET:
            field_dict["has_auth_header"] = has_auth_header
        if has_hmac_secret is not UNSET:
            field_dict["has_hmac_secret"] = has_hmac_secret
        if has_payload_template is not UNSET:
            field_dict["has_payload_template"] = has_payload_template
        if hmac_secret is not UNSET:
            field_dict["hmac_secret"] = hmac_secret
        if payload_template is not UNSET:
            field_dict["payload_template"] = payload_template
        if webhook_url is not UNSET:
            field_dict["webhook_url"] = webhook_url

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.channel_read_payload_template_type_0 import (
            ChannelReadPayloadTemplateType0,
        )

        d = dict(src_dict)

        def _parse_email_recipients(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        email_recipients = _parse_email_recipients(d.pop("email_recipients"))

        has_webhook = d.pop("has_webhook")

        id = UUID(d.pop("id"))

        name = d.pop("name")

        type_ = d.pop("type")

        def _parse_auth_header_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        auth_header_name = _parse_auth_header_name(d.pop("auth_header_name", UNSET))

        has_auth_header = d.pop("has_auth_header", UNSET)

        has_hmac_secret = d.pop("has_hmac_secret", UNSET)

        has_payload_template = d.pop("has_payload_template", UNSET)

        def _parse_hmac_secret(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        hmac_secret = _parse_hmac_secret(d.pop("hmac_secret", UNSET))

        def _parse_payload_template(data: object) -> ChannelReadPayloadTemplateType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                payload_template_type_0 = ChannelReadPayloadTemplateType0.from_dict(data)

                return payload_template_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ChannelReadPayloadTemplateType0 | None | Unset, data)

        payload_template = _parse_payload_template(d.pop("payload_template", UNSET))

        def _parse_webhook_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        webhook_url = _parse_webhook_url(d.pop("webhook_url", UNSET))

        channel_read = cls(
            email_recipients=email_recipients,
            has_webhook=has_webhook,
            id=id,
            name=name,
            type_=type_,
            auth_header_name=auth_header_name,
            has_auth_header=has_auth_header,
            has_hmac_secret=has_hmac_secret,
            has_payload_template=has_payload_template,
            hmac_secret=hmac_secret,
            payload_template=payload_template,
            webhook_url=webhook_url,
        )

        channel_read.additional_properties = d
        return channel_read

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
