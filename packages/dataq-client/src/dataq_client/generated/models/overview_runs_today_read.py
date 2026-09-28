from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="OverviewRunsTodayRead")


@_attrs_define
class OverviewRunsTodayRead:
    """Runs created since `since` (the start of the current UTC day, not the
    viewer's local day). `total` also counts queued and cancelled runs, so the three
    named states do not necessarily sum to it.

        Attributes:
            failed (int):
            running (int):
            since (datetime.datetime):
            succeeded (int):
            total (int):
    """

    failed: int
    running: int
    since: datetime.datetime
    succeeded: int
    total: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        failed = self.failed

        running = self.running

        since = self.since.isoformat()

        succeeded = self.succeeded

        total = self.total

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "failed": failed,
                "running": running,
                "since": since,
                "succeeded": succeeded,
                "total": total,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        failed = d.pop("failed")

        running = d.pop("running")

        since = datetime.datetime.fromisoformat(d.pop("since"))

        succeeded = d.pop("succeeded")

        total = d.pop("total")

        overview_runs_today_read = cls(
            failed=failed,
            running=running,
            since=since,
            succeeded=succeeded,
            total=total,
        )

        overview_runs_today_read.additional_properties = d
        return overview_runs_today_read

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
