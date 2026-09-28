from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.check_progress_read import CheckProgressRead
    from ..models.run_progress_read_counts import RunProgressReadCounts


T = TypeVar("T", bound="RunProgressRead")


@_attrs_define
class RunProgressRead:
    """Compact live-progress view for polling: run lifecycle + per-check
    resolution + a status histogram + elapsed time. Lighter than the full
    run+results detail. `completed_checks == 0` on a running run is NORMAL
    (checks resolve in batches, #318 — see `batched_pending`), not stuck.

        Attributes:
            checks (list[CheckProgressRead]):
            completed_checks (int):
            counts (RunProgressReadCounts):
            finished_at (datetime.datetime | None):
            run_id (UUID):
            started_at (datetime.datetime | None):
            status (str):
            suite_id (UUID):
            total_checks (int):
            batched_pending (bool | Unset):  Default: False.
            elapsed_ms (int | None | Unset):
            queued_reason (None | str | Unset):
    """

    checks: list[CheckProgressRead]
    completed_checks: int
    counts: RunProgressReadCounts
    finished_at: datetime.datetime | None
    run_id: UUID
    started_at: datetime.datetime | None
    status: str
    suite_id: UUID
    total_checks: int
    batched_pending: bool | Unset = False
    elapsed_ms: int | None | Unset = UNSET
    queued_reason: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        checks = []
        for checks_item_data in self.checks:
            checks_item = checks_item_data.to_dict()
            checks.append(checks_item)

        completed_checks = self.completed_checks

        counts = self.counts.to_dict()

        finished_at: None | str
        if isinstance(self.finished_at, datetime.datetime):
            finished_at = self.finished_at.isoformat()
        else:
            finished_at = self.finished_at

        run_id = str(self.run_id)

        started_at: None | str
        if isinstance(self.started_at, datetime.datetime):
            started_at = self.started_at.isoformat()
        else:
            started_at = self.started_at

        status = self.status

        suite_id = str(self.suite_id)

        total_checks = self.total_checks

        batched_pending = self.batched_pending

        elapsed_ms: int | None | Unset
        if isinstance(self.elapsed_ms, Unset):
            elapsed_ms = UNSET
        else:
            elapsed_ms = self.elapsed_ms

        queued_reason: None | str | Unset
        if isinstance(self.queued_reason, Unset):
            queued_reason = UNSET
        else:
            queued_reason = self.queued_reason

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "checks": checks,
                "completed_checks": completed_checks,
                "counts": counts,
                "finished_at": finished_at,
                "run_id": run_id,
                "started_at": started_at,
                "status": status,
                "suite_id": suite_id,
                "total_checks": total_checks,
            }
        )
        if batched_pending is not UNSET:
            field_dict["batched_pending"] = batched_pending
        if elapsed_ms is not UNSET:
            field_dict["elapsed_ms"] = elapsed_ms
        if queued_reason is not UNSET:
            field_dict["queued_reason"] = queued_reason

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.check_progress_read import CheckProgressRead
        from ..models.run_progress_read_counts import RunProgressReadCounts

        d = dict(src_dict)
        checks = []
        _checks = d.pop("checks")
        for checks_item_data in _checks:
            checks_item = CheckProgressRead.from_dict(checks_item_data)

            checks.append(checks_item)

        completed_checks = d.pop("completed_checks")

        counts = RunProgressReadCounts.from_dict(d.pop("counts"))

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

        run_id = UUID(d.pop("run_id"))

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

        total_checks = d.pop("total_checks")

        batched_pending = d.pop("batched_pending", UNSET)

        def _parse_elapsed_ms(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        elapsed_ms = _parse_elapsed_ms(d.pop("elapsed_ms", UNSET))

        def _parse_queued_reason(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        queued_reason = _parse_queued_reason(d.pop("queued_reason", UNSET))

        run_progress_read = cls(
            checks=checks,
            completed_checks=completed_checks,
            counts=counts,
            finished_at=finished_at,
            run_id=run_id,
            started_at=started_at,
            status=status,
            suite_id=suite_id,
            total_checks=total_checks,
            batched_pending=batched_pending,
            elapsed_ms=elapsed_ms,
            queued_reason=queued_reason,
        )

        run_progress_read.additional_properties = d
        return run_progress_read

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
