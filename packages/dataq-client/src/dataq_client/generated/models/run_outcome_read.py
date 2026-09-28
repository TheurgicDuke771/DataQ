from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="RunOutcomeRead")


@_attrs_define
class RunOutcomeRead:
    """A suite's latest run outcome — execution status + the DQ summary.

    Attributes:
        checks_passed (int):
        checks_total (int):
        created_at (datetime.datetime | None):
        finished_at (datetime.datetime | None):
        run_id (None | UUID):
        status (None | str):
        worst_severity (None | str):
    """

    checks_passed: int
    checks_total: int
    created_at: datetime.datetime | None
    finished_at: datetime.datetime | None
    run_id: None | UUID
    status: None | str
    worst_severity: None | str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        checks_passed = self.checks_passed

        checks_total = self.checks_total

        created_at: None | str
        if isinstance(self.created_at, datetime.datetime):
            created_at = self.created_at.isoformat()
        else:
            created_at = self.created_at

        finished_at: None | str
        if isinstance(self.finished_at, datetime.datetime):
            finished_at = self.finished_at.isoformat()
        else:
            finished_at = self.finished_at

        run_id: None | str
        if isinstance(self.run_id, UUID):
            run_id = str(self.run_id)
        else:
            run_id = self.run_id

        status: None | str
        status = self.status

        worst_severity: None | str
        worst_severity = self.worst_severity

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "checks_passed": checks_passed,
                "checks_total": checks_total,
                "created_at": created_at,
                "finished_at": finished_at,
                "run_id": run_id,
                "status": status,
                "worst_severity": worst_severity,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        checks_passed = d.pop("checks_passed")

        checks_total = d.pop("checks_total")

        def _parse_created_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                created_at_type_0 = datetime.datetime.fromisoformat(data)

                return created_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        created_at = _parse_created_at(d.pop("created_at"))

        def _parse_finished_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                finished_at_type_0 = datetime.datetime.fromisoformat(data)

                return finished_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        finished_at = _parse_finished_at(d.pop("finished_at"))

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

        def _parse_status(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        status = _parse_status(d.pop("status"))

        def _parse_worst_severity(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        worst_severity = _parse_worst_severity(d.pop("worst_severity"))

        run_outcome_read = cls(
            checks_passed=checks_passed,
            checks_total=checks_total,
            created_at=created_at,
            finished_at=finished_at,
            run_id=run_id,
            status=status,
            worst_severity=worst_severity,
        )

        run_outcome_read.additional_properties = d
        return run_outcome_read

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
