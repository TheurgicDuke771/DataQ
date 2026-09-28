from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.overview_incidents_read import OverviewIncidentsRead
    from ..models.overview_members_read import OverviewMembersRead
    from ..models.overview_runs_today_read import OverviewRunsTodayRead
    from ..models.overview_suites_read import OverviewSuitesRead


T = TypeVar("T", bound="AdminOverviewRead")


@_attrs_define
class AdminOverviewRead:
    """The four Overview stat cards, workspace-wide (#1696) — counted over every
    suite/run/incident in the workspace, not the caller's grants.

        Attributes:
            generated_at (datetime.datetime):
            incidents (OverviewIncidentsRead): `open` counts every UNRESOLVED incident, and `acknowledged` is the subset of
                those someone has picked up — so `acknowledged` is never larger than `open`, and
                an acknowledged incident is still open (acknowledging silences nothing).
            members (OverviewMembersRead): Workspace membership. `pending_first_signin` is `null` — never `0` — while
                DataQ has no invite record to count: a user row is created BY the first
                successful sign-in, so an admitted-but-never-signed-in person leaves no trace
                here. `pending_source` says which it is.
            runs_today (OverviewRunsTodayRead): Runs created since `since` (the start of the current UTC day, not the
                viewer's local day). `total` also counts queued and cancelled runs, so the three
                named states do not necessarily sum to it.
            suites (OverviewSuitesRead): `connections` is the number of DISTINCT connections the suites target, not
                the number of connections configured — a connection no suite runs against is
                not counted.
    """

    generated_at: datetime.datetime
    incidents: OverviewIncidentsRead
    members: OverviewMembersRead
    runs_today: OverviewRunsTodayRead
    suites: OverviewSuitesRead
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        generated_at = self.generated_at.isoformat()

        incidents = self.incidents.to_dict()

        members = self.members.to_dict()

        runs_today = self.runs_today.to_dict()

        suites = self.suites.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "generated_at": generated_at,
                "incidents": incidents,
                "members": members,
                "runs_today": runs_today,
                "suites": suites,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.overview_incidents_read import OverviewIncidentsRead
        from ..models.overview_members_read import OverviewMembersRead
        from ..models.overview_runs_today_read import OverviewRunsTodayRead
        from ..models.overview_suites_read import OverviewSuitesRead

        d = dict(src_dict)
        generated_at = datetime.datetime.fromisoformat(d.pop("generated_at"))

        incidents = OverviewIncidentsRead.from_dict(d.pop("incidents"))

        members = OverviewMembersRead.from_dict(d.pop("members"))

        runs_today = OverviewRunsTodayRead.from_dict(d.pop("runs_today"))

        suites = OverviewSuitesRead.from_dict(d.pop("suites"))

        admin_overview_read = cls(
            generated_at=generated_at,
            incidents=incidents,
            members=members,
            runs_today=runs_today,
            suites=suites,
        )

        admin_overview_read.additional_properties = d
        return admin_overview_read

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
