from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.client_event_attributes import ClientEventAttributes


T = TypeVar("T", bound="ClientEvent")


@_attrs_define
class ClientEvent:
    """
    Attributes:
        name (str):
        attributes (ClientEventAttributes | Unset):
    """

    name: str
    attributes: ClientEventAttributes | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        attributes: dict[str, Any] | Unset = UNSET
        if not isinstance(self.attributes, Unset):
            attributes = self.attributes.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "name": name,
            }
        )
        if attributes is not UNSET:
            field_dict["attributes"] = attributes

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.client_event_attributes import ClientEventAttributes

        d = dict(src_dict)
        name = d.pop("name")

        _attributes = d.pop("attributes", UNSET)
        attributes: ClientEventAttributes | Unset
        if isinstance(_attributes, Unset):
            attributes = UNSET
        else:
            attributes = ClientEventAttributes.from_dict(_attributes)

        client_event = cls(
            name=name,
            attributes=attributes,
        )

        return client_event
