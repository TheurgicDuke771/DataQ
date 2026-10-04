from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="RunRead")


@_attrs_define
class RunRead:
    """A DQ suite run (execution lifecycle; `status` is execution, not pass/fail).

    Attributes:
        created_at (datetime.datetime):
        finished_at (datetime.datetime | None):
        id (UUID):
        started_at (datetime.datetime | None):
        status (str):
        suite_id (UUID):
        triggered_by (None | str):
        asset_id (None | Unset | UUID):
        checks_passed (int | Unset):  Default: 0.
        checks_total (int | Unset):  Default: 0.
        failure_reason (None | str | Unset):
        queued_reason (None | str | Unset):
        triggered_by_label (None | str | Unset):
        worst_severity (None | str | Unset):
    """

    created_at: datetime.datetime
    finished_at: datetime.datetime | None
    id: UUID
    started_at: datetime.datetime | None
    status: str
    suite_id: UUID
    triggered_by: None | str
    asset_id: None | Unset | UUID = UNSET
    checks_passed: int | Unset = 0
    checks_total: int | Unset = 0
    failure_reason: None | str | Unset = UNSET
    queued_reason: None | str | Unset = UNSET
    triggered_by_label: None | str | Unset = UNSET
    worst_severity: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        finished_at: None | str
        if isinstance(self.finished_at, datetime.datetime):
            finished_at = self.finished_at.isoformat()
        else:
            finished_at = self.finished_at

        id = str(self.id)

        started_at: None | str
        if isinstance(self.started_at, datetime.datetime):
            started_at = self.started_at.isoformat()
        else:
            started_at = self.started_at

        status = self.status

        suite_id = str(self.suite_id)

        triggered_by: None | str
        triggered_by = self.triggered_by

        asset_id: None | str | Unset
        if isinstance(self.asset_id, Unset):
            asset_id = UNSET
        elif isinstance(self.asset_id, UUID):
            asset_id = str(self.asset_id)
        else:
            asset_id = self.asset_id

        checks_passed = self.checks_passed

        checks_total = self.checks_total

        failure_reason: None | str | Unset
        if isinstance(self.failure_reason, Unset):
            failure_reason = UNSET
        else:
            failure_reason = self.failure_reason

        queued_reason: None | str | Unset
        if isinstance(self.queued_reason, Unset):
            queued_reason = UNSET
        else:
            queued_reason = self.queued_reason

        triggered_by_label: None | str | Unset
        if isinstance(self.triggered_by_label, Unset):
            triggered_by_label = UNSET
        else:
            triggered_by_label = self.triggered_by_label

        worst_severity: None | str | Unset
        if isinstance(self.worst_severity, Unset):
            worst_severity = UNSET
        else:
            worst_severity = self.worst_severity

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "finished_at": finished_at,
                "id": id,
                "started_at": started_at,
                "status": status,
                "suite_id": suite_id,
                "triggered_by": triggered_by,
            }
        )
        if asset_id is not UNSET:
            field_dict["asset_id"] = asset_id
        if checks_passed is not UNSET:
            field_dict["checks_passed"] = checks_passed
        if checks_total is not UNSET:
            field_dict["checks_total"] = checks_total
        if failure_reason is not UNSET:
            field_dict["failure_reason"] = failure_reason
        if queued_reason is not UNSET:
            field_dict["queued_reason"] = queued_reason
        if triggered_by_label is not UNSET:
            field_dict["triggered_by_label"] = triggered_by_label
        if worst_severity is not UNSET:
            field_dict["worst_severity"] = worst_severity

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

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

        id = UUID(d.pop("id"))

        def _parse_started_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                started_at_type_0 = datetime.datetime.fromisoformat(data)

                return started_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        started_at = _parse_started_at(d.pop("started_at"))

        status = d.pop("status")

        suite_id = UUID(d.pop("suite_id"))

        def _parse_triggered_by(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        triggered_by = _parse_triggered_by(d.pop("triggered_by"))

        def _parse_asset_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                asset_id_type_0 = UUID(data)

                return asset_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | Unset | UUID, data)

        asset_id = _parse_asset_id(d.pop("asset_id", UNSET))

        checks_passed = d.pop("checks_passed", UNSET)

        checks_total = d.pop("checks_total", UNSET)

        def _parse_failure_reason(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        failure_reason = _parse_failure_reason(d.pop("failure_reason", UNSET))

        def _parse_queued_reason(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        queued_reason = _parse_queued_reason(d.pop("queued_reason", UNSET))

        def _parse_triggered_by_label(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        triggered_by_label = _parse_triggered_by_label(d.pop("triggered_by_label", UNSET))

        def _parse_worst_severity(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        worst_severity = _parse_worst_severity(d.pop("worst_severity", UNSET))

        run_read = cls(
            created_at=created_at,
            finished_at=finished_at,
            id=id,
            started_at=started_at,
            status=status,
            suite_id=suite_id,
            triggered_by=triggered_by,
            asset_id=asset_id,
            checks_passed=checks_passed,
            checks_total=checks_total,
            failure_reason=failure_reason,
            queued_reason=queued_reason,
            triggered_by_label=triggered_by_label,
            worst_severity=worst_severity,
        )

        run_read.additional_properties = d
        return run_read

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
