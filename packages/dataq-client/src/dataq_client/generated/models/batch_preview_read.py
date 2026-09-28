from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="BatchPreviewRead")


@_attrs_define
class BatchPreviewRead:
    """The concrete file path a batch-target spec resolves to right now — the same
    live resolution `run_target.materialize_path` performs at run time, run early
    and without persisting anything (#1193). Callers re-request on every field
    change to keep the "resolves to" hint live while authoring.

    Unlike the run path, this listing is budget-bounded (#1243) — `truncated`
    says whether the object/wall-clock budget cut the scan short before it could
    see every object under the prefix, in which case `path` is only the best
    match seen among the first `scanned` objects, not a guaranteed answer.

        Attributes:
            path (None | str):
            scanned (int):
            truncated (bool):
    """

    path: None | str
    scanned: int
    truncated: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        path: None | str
        path = self.path

        scanned = self.scanned

        truncated = self.truncated

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "path": path,
                "scanned": scanned,
                "truncated": truncated,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)

        def _parse_path(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        path = _parse_path(d.pop("path"))

        scanned = d.pop("scanned")

        truncated = d.pop("truncated")

        batch_preview_read = cls(
            path=path,
            scanned=scanned,
            truncated=truncated,
        )

        batch_preview_read.additional_properties = d
        return batch_preview_read

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
