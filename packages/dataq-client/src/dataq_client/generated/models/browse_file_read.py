from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="BrowseFileRead")


@_attrs_define
class BrowseFileRead:
    """
    Attributes:
        last_modified (datetime.datetime | None):
        path (str):
        size (int | None):
    """

    last_modified: datetime.datetime | None
    path: str
    size: int | None
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        last_modified: None | str
        if isinstance(self.last_modified, datetime.datetime):
            last_modified = self.last_modified.isoformat()
        else:
            last_modified = self.last_modified

        path = self.path

        size: int | None
        size = self.size

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "last_modified": last_modified,
                "path": path,
                "size": size,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)

        def _parse_last_modified(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_modified_type_0 = datetime.datetime.fromisoformat(data)

                return last_modified_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        last_modified = _parse_last_modified(d.pop("last_modified"))

        path = d.pop("path")

        def _parse_size(data: object) -> int | None:
            if data is None:
                return data
            return cast(int | None, data)

        size = _parse_size(d.pop("size"))

        browse_file_read = cls(
            last_modified=last_modified,
            path=path,
            size=size,
        )

        browse_file_read.additional_properties = d
        return browse_file_read

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
