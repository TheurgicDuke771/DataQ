from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.contract_read import ContractRead


T = TypeVar("T", bound="ContractImportRead")


@_attrs_define
class ContractImportRead:
    """
    Attributes:
        checks_added (int):
        checks_disabled (int):
        checks_updated (int):
        contract (ContractRead):
        created_suite_ids (list[UUID]):
        reconciled_suite_ids (list[UUID]):
        untargeted_objects (list[str]):
        warnings (list[str]):
    """

    checks_added: int
    checks_disabled: int
    checks_updated: int
    contract: ContractRead
    created_suite_ids: list[UUID]
    reconciled_suite_ids: list[UUID]
    untargeted_objects: list[str]
    warnings: list[str]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        checks_added = self.checks_added

        checks_disabled = self.checks_disabled

        checks_updated = self.checks_updated

        contract = self.contract.to_dict()

        created_suite_ids = []
        for created_suite_ids_item_data in self.created_suite_ids:
            created_suite_ids_item = str(created_suite_ids_item_data)
            created_suite_ids.append(created_suite_ids_item)

        reconciled_suite_ids = []
        for reconciled_suite_ids_item_data in self.reconciled_suite_ids:
            reconciled_suite_ids_item = str(reconciled_suite_ids_item_data)
            reconciled_suite_ids.append(reconciled_suite_ids_item)

        untargeted_objects = self.untargeted_objects

        warnings = self.warnings

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "checks_added": checks_added,
                "checks_disabled": checks_disabled,
                "checks_updated": checks_updated,
                "contract": contract,
                "created_suite_ids": created_suite_ids,
                "reconciled_suite_ids": reconciled_suite_ids,
                "untargeted_objects": untargeted_objects,
                "warnings": warnings,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.contract_read import ContractRead

        d = dict(src_dict)
        checks_added = d.pop("checks_added")

        checks_disabled = d.pop("checks_disabled")

        checks_updated = d.pop("checks_updated")

        contract = ContractRead.from_dict(d.pop("contract"))

        created_suite_ids = []
        _created_suite_ids = d.pop("created_suite_ids")
        for created_suite_ids_item_data in _created_suite_ids:
            created_suite_ids_item = UUID(created_suite_ids_item_data)

            created_suite_ids.append(created_suite_ids_item)

        reconciled_suite_ids = []
        _reconciled_suite_ids = d.pop("reconciled_suite_ids")
        for reconciled_suite_ids_item_data in _reconciled_suite_ids:
            reconciled_suite_ids_item = UUID(reconciled_suite_ids_item_data)

            reconciled_suite_ids.append(reconciled_suite_ids_item)

        untargeted_objects = cast(list[str], d.pop("untargeted_objects"))

        warnings = cast(list[str], d.pop("warnings"))

        contract_import_read = cls(
            checks_added=checks_added,
            checks_disabled=checks_disabled,
            checks_updated=checks_updated,
            contract=contract,
            created_suite_ids=created_suite_ids,
            reconciled_suite_ids=reconciled_suite_ids,
            untargeted_objects=untargeted_objects,
            warnings=warnings,
        )

        contract_import_read.additional_properties = d
        return contract_import_read

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
