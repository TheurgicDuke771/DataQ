from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="ContractSuiteRead")


@_attrs_define
class ContractSuiteRead:
    """
    Attributes:
        contract_object (None | str):
        id (UUID):
        name (str):
        runnable (bool):
    """

    contract_object: None | str
    id: UUID
    name: str
    runnable: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        contract_object: None | str
        contract_object = self.contract_object

        id = str(self.id)

        name = self.name

        runnable = self.runnable

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "contract_object": contract_object,
                "id": id,
                "name": name,
                "runnable": runnable,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)

        def _parse_contract_object(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        contract_object = _parse_contract_object(d.pop("contract_object"))

        id = UUID(d.pop("id"))

        name = d.pop("name")

        runnable = d.pop("runnable")

        contract_suite_read = cls(
            contract_object=contract_object,
            id=id,
            name=name,
            runnable=runnable,
        )

        contract_suite_read.additional_properties = d
        return contract_suite_read

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
