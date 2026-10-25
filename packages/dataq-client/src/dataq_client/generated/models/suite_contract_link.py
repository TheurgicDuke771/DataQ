from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from typing_extensions import Self

T = TypeVar("T", bound="SuiteContractLink")


@_attrs_define
class SuiteContractLink:
    """The data contract a suite enforces (ADR 0052) and its ODCS schema object.

    Attributes:
        contract_id (UUID):
        contract_object (str):
    """

    contract_id: UUID
    contract_object: str

    def to_dict(self) -> dict[str, Any]:
        contract_id = str(self.contract_id)

        contract_object = self.contract_object

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "contract_id": contract_id,
                "contract_object": contract_object,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        contract_id = UUID(d.pop("contract_id"))

        contract_object = d.pop("contract_object")

        suite_contract_link = cls(
            contract_id=contract_id,
            contract_object=contract_object,
        )

        return suite_contract_link
