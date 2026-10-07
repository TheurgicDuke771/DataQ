from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.channel_read import ChannelRead


T = TypeVar("T", bound="PromotedChannelRead")


@_attrs_define
class PromotedChannelRead:
    """
    Attributes:
        channel (ChannelRead): A channel's shape as any authenticated caller may see it. The Teams/Slack
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
        workspace_default_now_applies (bool):
    """

    channel: ChannelRead
    workspace_default_now_applies: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        channel = self.channel.to_dict()

        workspace_default_now_applies = self.workspace_default_now_applies

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "channel": channel,
                "workspace_default_now_applies": workspace_default_now_applies,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.channel_read import ChannelRead

        d = dict(src_dict)
        channel = ChannelRead.from_dict(d.pop("channel"))

        workspace_default_now_applies = d.pop("workspace_default_now_applies")

        promoted_channel_read = cls(
            channel=channel,
            workspace_default_now_applies=workspace_default_now_applies,
        )

        promoted_channel_read.additional_properties = d
        return promoted_channel_read

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
