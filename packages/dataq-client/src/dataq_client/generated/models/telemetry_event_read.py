from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.telemetry_event_read_attributes import TelemetryEventReadAttributes


T = TypeVar("T", bound="TelemetryEventRead")


@_attrs_define
class TelemetryEventRead:
    """
    Attributes:
        attributes (TelemetryEventReadAttributes):
        name (str):
    """

    attributes: TelemetryEventReadAttributes
    name: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        attributes = self.attributes.to_dict()

        name = self.name

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "attributes": attributes,
                "name": name,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.telemetry_event_read_attributes import (
            TelemetryEventReadAttributes,
        )

        d = dict(src_dict)
        attributes = TelemetryEventReadAttributes.from_dict(d.pop("attributes"))

        name = d.pop("name")

        telemetry_event_read = cls(
            attributes=attributes,
            name=name,
        )

        telemetry_event_read.additional_properties = d
        return telemetry_event_read

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
