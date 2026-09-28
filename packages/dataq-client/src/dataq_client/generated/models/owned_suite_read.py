from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="OwnedSuiteRead")


@_attrs_define
class OwnedSuiteRead:
    """
    Attributes:
        check_count (int):
        id (UUID):
        name (str):
        result_count (int):
        run_count (int):
    """

    check_count: int
    id: UUID
    name: str
    result_count: int
    run_count: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        check_count = self.check_count

        id = str(self.id)

        name = self.name

        result_count = self.result_count

        run_count = self.run_count

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "check_count": check_count,
                "id": id,
                "name": name,
                "result_count": result_count,
                "run_count": run_count,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        check_count = d.pop("check_count")

        id = UUID(d.pop("id"))

        name = d.pop("name")

        result_count = d.pop("result_count")

        run_count = d.pop("run_count")

        owned_suite_read = cls(
            check_count=check_count,
            id=id,
            name=name,
            result_count=result_count,
            run_count=run_count,
        )

        owned_suite_read.additional_properties = d
        return owned_suite_read

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
