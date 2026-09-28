from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="SuitePerformanceRead")


@_attrs_define
class SuitePerformanceRead:
    """
    Attributes:
        name (str):
        score (float | None):
        state (str):
        suite_id (UUID):
    """

    name: str
    score: float | None
    state: str
    suite_id: UUID
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        score: float | None
        score = self.score

        state = self.state

        suite_id = str(self.suite_id)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
                "score": score,
                "state": state,
                "suite_id": suite_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        name = d.pop("name")

        def _parse_score(data: object) -> float | None:
            if data is None:
                return data
            return cast(float | None, data)

        score = _parse_score(d.pop("score"))

        state = d.pop("state")

        suite_id = UUID(d.pop("suite_id"))

        suite_performance_read = cls(
            name=name,
            score=score,
            state=state,
            suite_id=suite_id,
        )

        suite_performance_read.additional_properties = d
        return suite_performance_read

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
