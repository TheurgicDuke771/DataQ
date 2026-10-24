from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.tag_usage_read import TagUsageRead


T = TypeVar("T", bound="TagRead")


@_attrs_define
class TagRead:
    """
    Attributes:
        created_at (datetime.datetime):
        id (UUID):
        name (str):
        usage (TagUsageRead): How many of each entity carry the tag — workspace-wide, including suites and checks the
            caller cannot open. Counts only: which entities carry it is read from the entities, where the
            suite grant applies.
    """

    created_at: datetime.datetime
    id: UUID
    name: str
    usage: TagUsageRead
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        id = str(self.id)

        name = self.name

        usage = self.usage.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "id": id,
                "name": name,
                "usage": usage,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.tag_usage_read import TagUsageRead

        d = dict(src_dict)
        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        id = UUID(d.pop("id"))

        name = d.pop("name")

        usage = TagUsageRead.from_dict(d.pop("usage"))

        tag_read = cls(
            created_at=created_at,
            id=id,
            name=name,
            usage=usage,
        )

        tag_read.additional_properties = d
        return tag_read

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
