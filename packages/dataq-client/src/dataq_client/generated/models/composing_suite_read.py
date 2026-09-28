from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.run_outcome_read import RunOutcomeRead


T = TypeVar("T", bound="ComposingSuiteRead")


@_attrs_define
class ComposingSuiteRead:
    """One suite the caller can see that targets the asset, with its latest run.

    Attributes:
        latest_run (RunOutcomeRead): A suite's latest run outcome — execution status + the DQ summary.
        my_permission (str):
        name (str):
        suite_id (UUID):
    """

    latest_run: RunOutcomeRead
    my_permission: str
    name: str
    suite_id: UUID
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        latest_run = self.latest_run.to_dict()

        my_permission = self.my_permission

        name = self.name

        suite_id = str(self.suite_id)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "latest_run": latest_run,
                "my_permission": my_permission,
                "name": name,
                "suite_id": suite_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.run_outcome_read import RunOutcomeRead

        d = dict(src_dict)
        latest_run = RunOutcomeRead.from_dict(d.pop("latest_run"))

        my_permission = d.pop("my_permission")

        name = d.pop("name")

        suite_id = UUID(d.pop("suite_id"))

        composing_suite_read = cls(
            latest_run=latest_run,
            my_permission=my_permission,
            name=name,
            suite_id=suite_id,
        )

        composing_suite_read.additional_properties = d
        return composing_suite_read

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
