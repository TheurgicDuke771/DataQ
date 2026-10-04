from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.check_change_read import CheckChangeRead


T = TypeVar("T", bound="SuiteApplyRead")


@_attrs_define
class SuiteApplyRead:
    """The plan, and whether it was carried out. With `dry_run` nothing was written and
    `changed` says whether the suite differs from the document (drift). Without it,
    `changed` says whether anything was written.

    `unmanaged` lists checks the suite has that the document does not name. They were
    left alone because `prune` was false; they are NOT counted in `changed`.

        Attributes:
            changed (bool):
            checks (list[CheckChangeRead]):
            dry_run (bool):
            suite_fields (list[str]):
            unmanaged (list[str]):
    """

    changed: bool
    checks: list[CheckChangeRead]
    dry_run: bool
    suite_fields: list[str]
    unmanaged: list[str]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        changed = self.changed

        checks = []
        for checks_item_data in self.checks:
            checks_item = checks_item_data.to_dict()
            checks.append(checks_item)

        dry_run = self.dry_run

        suite_fields = self.suite_fields

        unmanaged = self.unmanaged

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "changed": changed,
                "checks": checks,
                "dry_run": dry_run,
                "suite_fields": suite_fields,
                "unmanaged": unmanaged,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.check_change_read import CheckChangeRead

        d = dict(src_dict)
        changed = d.pop("changed")

        checks = []
        _checks = d.pop("checks")
        for checks_item_data in _checks:
            checks_item = CheckChangeRead.from_dict(checks_item_data)

            checks.append(checks_item)

        dry_run = d.pop("dry_run")

        suite_fields = cast(list[str], d.pop("suite_fields"))

        unmanaged = cast(list[str], d.pop("unmanaged"))

        suite_apply_read = cls(
            changed=changed,
            checks=checks,
            dry_run=dry_run,
            suite_fields=suite_fields,
            unmanaged=unmanaged,
        )

        suite_apply_read.additional_properties = d
        return suite_apply_read

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
