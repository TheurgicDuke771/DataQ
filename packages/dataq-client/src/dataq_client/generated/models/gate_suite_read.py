from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.gate_suite_read_state import GateSuiteReadState

T = TypeVar("T", bound="GateSuiteRead")


@_attrs_define
class GateSuiteRead:
    """
    Attributes:
        checks_passed (int):
        checks_total (int):
        has_error (bool):
        run_id (None | UUID):
        run_status (None | str):
        state (GateSuiteReadState):
        suite_id (UUID):
        worst_severity (None | str):
    """

    checks_passed: int
    checks_total: int
    has_error: bool
    run_id: None | UUID
    run_status: None | str
    state: GateSuiteReadState
    suite_id: UUID
    worst_severity: None | str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        checks_passed = self.checks_passed

        checks_total = self.checks_total

        has_error = self.has_error

        run_id: None | str
        if isinstance(self.run_id, UUID):
            run_id = str(self.run_id)
        else:
            run_id = self.run_id

        run_status: None | str
        run_status = self.run_status

        state = self.state.value

        suite_id = str(self.suite_id)

        worst_severity: None | str
        worst_severity = self.worst_severity

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "checks_passed": checks_passed,
                "checks_total": checks_total,
                "has_error": has_error,
                "run_id": run_id,
                "run_status": run_status,
                "state": state,
                "suite_id": suite_id,
                "worst_severity": worst_severity,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        checks_passed = d.pop("checks_passed")

        checks_total = d.pop("checks_total")

        has_error = d.pop("has_error")

        def _parse_run_id(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                run_id_type_0 = UUID(data)

                return run_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | UUID, data)

        run_id = _parse_run_id(d.pop("run_id"))

        def _parse_run_status(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        run_status = _parse_run_status(d.pop("run_status"))

        state = GateSuiteReadState(d.pop("state"))

        suite_id = UUID(d.pop("suite_id"))

        def _parse_worst_severity(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        worst_severity = _parse_worst_severity(d.pop("worst_severity"))

        gate_suite_read = cls(
            checks_passed=checks_passed,
            checks_total=checks_total,
            has_error=has_error,
            run_id=run_id,
            run_status=run_status,
            state=state,
            suite_id=suite_id,
            worst_severity=worst_severity,
        )

        gate_suite_read.additional_properties = d
        return gate_suite_read

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
