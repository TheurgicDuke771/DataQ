from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from typing_extensions import Self

from ..models.promote_destination_destination import PromoteDestinationDestination

T = TypeVar("T", bound="PromoteDestination")


@_attrs_define
class PromoteDestination:
    """
    Attributes:
        destination (PromoteDestinationDestination): Which of the suite's legacy inline destinations to move
        name (str): Name for the new channel
    """

    destination: PromoteDestinationDestination
    name: str

    def to_dict(self) -> dict[str, Any]:
        destination = self.destination.value

        name = self.name

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "destination": destination,
                "name": name,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        destination = PromoteDestinationDestination(d.pop("destination"))

        name = d.pop("name")

        promote_destination = cls(
            destination=destination,
            name=name,
        )

        return promote_destination
