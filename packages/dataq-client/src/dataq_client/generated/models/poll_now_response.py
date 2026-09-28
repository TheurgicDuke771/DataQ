from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.poll_dispatch_read import PollDispatchRead


T = TypeVar("T", bound="PollNowResponse")


@_attrs_define
class PollNowResponse:
    """Poll-now dispatch (#1701). `dispatched` lists what actually reached the broker;
    an empty list with no error means there were no orchestration connections to poll.

    Enqueued is not polled: the worker runs these afterwards, so `GET /admin/health`
    still shows the previous poll timestamps until it does.

        Attributes:
            dispatched (list[PollDispatchRead]):
            requested_at (datetime.datetime):
    """

    dispatched: list[PollDispatchRead]
    requested_at: datetime.datetime
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        dispatched = []
        for dispatched_item_data in self.dispatched:
            dispatched_item = dispatched_item_data.to_dict()
            dispatched.append(dispatched_item)

        requested_at = self.requested_at.isoformat()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "dispatched": dispatched,
                "requested_at": requested_at,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.poll_dispatch_read import PollDispatchRead

        d = dict(src_dict)
        dispatched = []
        _dispatched = d.pop("dispatched")
        for dispatched_item_data in _dispatched:
            dispatched_item = PollDispatchRead.from_dict(dispatched_item_data)

            dispatched.append(dispatched_item)

        requested_at = datetime.datetime.fromisoformat(d.pop("requested_at"))

        poll_now_response = cls(
            dispatched=dispatched,
            requested_at=requested_at,
        )

        poll_now_response.additional_properties = d
        return poll_now_response

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
