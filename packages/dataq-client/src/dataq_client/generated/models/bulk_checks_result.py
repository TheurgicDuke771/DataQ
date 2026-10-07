from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.check_read import CheckRead


T = TypeVar("T", bound="BulkChecksResult")


@_attrs_define
class BulkChecksResult:
    """`affected` is how many checks were changed. It is every check named in the
    request (a bulk call changes all of them or, on any refusal, none), except for
    `checks-bulk/enabled`, which leaves out a check already in the requested state.

        Attributes:
            affected (int):
            checks (list[CheckRead] | Unset):
    """

    affected: int
    checks: list[CheckRead] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        affected = self.affected

        checks: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.checks, Unset):
            checks = []
            for checks_item_data in self.checks:
                checks_item = checks_item_data.to_dict()
                checks.append(checks_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "affected": affected,
            }
        )
        if checks is not UNSET:
            field_dict["checks"] = checks

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.check_read import CheckRead

        d = dict(src_dict)
        affected = d.pop("affected")

        _checks = d.pop("checks", UNSET)
        checks: list[CheckRead] | Unset = UNSET
        if _checks is not UNSET:
            checks = []
            for checks_item_data in _checks:
                checks_item = CheckRead.from_dict(checks_item_data)

                checks.append(checks_item)

        bulk_checks_result = cls(
            affected=affected,
            checks=checks,
        )

        bulk_checks_result.additional_properties = d
        return bulk_checks_result

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
