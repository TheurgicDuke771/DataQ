from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.browse_file_read import BrowseFileRead


T = TypeVar("T", bound="FileBrowseRead")


@_attrs_define
class FileBrowseRead:
    """The folders and files directly under `prefix` in the connection's container/bucket
    (`root`). `truncated` means the level holds more than `limit` entries.

        Attributes:
            files (list[BrowseFileRead]):
            folders (list[str]):
            limit (int):
            prefix (str):
            root (str):
            truncated (bool):
    """

    files: list[BrowseFileRead]
    folders: list[str]
    limit: int
    prefix: str
    root: str
    truncated: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        files = []
        for files_item_data in self.files:
            files_item = files_item_data.to_dict()
            files.append(files_item)

        folders = self.folders

        limit = self.limit

        prefix = self.prefix

        root = self.root

        truncated = self.truncated

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "files": files,
                "folders": folders,
                "limit": limit,
                "prefix": prefix,
                "root": root,
                "truncated": truncated,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.browse_file_read import BrowseFileRead

        d = dict(src_dict)
        files = []
        _files = d.pop("files")
        for files_item_data in _files:
            files_item = BrowseFileRead.from_dict(files_item_data)

            files.append(files_item)

        folders = cast(list[str], d.pop("folders"))

        limit = d.pop("limit")

        prefix = d.pop("prefix")

        root = d.pop("root")

        truncated = d.pop("truncated")

        file_browse_read = cls(
            files=files,
            folders=folders,
            limit=limit,
            prefix=prefix,
            root=root,
            truncated=truncated,
        )

        file_browse_read.additional_properties = d
        return file_browse_read

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
