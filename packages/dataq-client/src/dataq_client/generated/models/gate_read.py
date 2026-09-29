from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.gate_read_state import GateReadState

if TYPE_CHECKING:
    from ..models.gate_suite_read import GateSuiteRead


T = TypeVar("T", bound="GateRead")


@_attrs_define
class GateRead:
    """
    Attributes:
        created_runs (int):
        fail_on (str):
        retry_after_seconds (int | None):
        state (GateReadState):
        suites (list[GateSuiteRead]):
        triggered_by (str):
    """

    created_runs: int
    fail_on: str
    retry_after_seconds: int | None
    state: GateReadState
    suites: list[GateSuiteRead]
    triggered_by: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_runs = self.created_runs

        fail_on = self.fail_on

        retry_after_seconds: int | None
        retry_after_seconds = self.retry_after_seconds

        state = self.state.value

        suites = []
        for suites_item_data in self.suites:
            suites_item = suites_item_data.to_dict()
            suites.append(suites_item)

        triggered_by = self.triggered_by

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_runs": created_runs,
                "fail_on": fail_on,
                "retry_after_seconds": retry_after_seconds,
                "state": state,
                "suites": suites,
                "triggered_by": triggered_by,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.gate_suite_read import GateSuiteRead

        d = dict(src_dict)
        created_runs = d.pop("created_runs")

        fail_on = d.pop("fail_on")

        def _parse_retry_after_seconds(data: object) -> int | None:
            if data is None:
                return data
            return cast(int | None, data)

        retry_after_seconds = _parse_retry_after_seconds(d.pop("retry_after_seconds"))

        state = GateReadState(d.pop("state"))

        suites = []
        _suites = d.pop("suites")
        for suites_item_data in _suites:
            suites_item = GateSuiteRead.from_dict(suites_item_data)

            suites.append(suites_item)

        triggered_by = d.pop("triggered_by")

        gate_read = cls(
            created_runs=created_runs,
            fail_on=fail_on,
            retry_after_seconds=retry_after_seconds,
            state=state,
            suites=suites,
            triggered_by=triggered_by,
        )

        gate_read.additional_properties = d
        return gate_read

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
