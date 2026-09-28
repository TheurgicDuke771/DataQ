from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="OverviewMembersRead")


@_attrs_define
class OverviewMembersRead:
    """Workspace membership. `pending_first_signin` is `null` — never `0` — while
    DataQ has no invite record to count: a user row is created BY the first
    successful sign-in, so an admitted-but-never-signed-in person leaves no trace
    here. `pending_source` says which it is.

        Attributes:
            total (int):
            pending_first_signin (int | None | Unset):
            pending_source (Literal['not_available'] | Unset):  Default: 'not_available'.
    """

    total: int
    pending_first_signin: int | None | Unset = UNSET
    pending_source: Literal["not_available"] | Unset = "not_available"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        total = self.total

        pending_first_signin: int | None | Unset
        if isinstance(self.pending_first_signin, Unset):
            pending_first_signin = UNSET
        else:
            pending_first_signin = self.pending_first_signin

        pending_source = self.pending_source

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "total": total,
            }
        )
        if pending_first_signin is not UNSET:
            field_dict["pending_first_signin"] = pending_first_signin
        if pending_source is not UNSET:
            field_dict["pending_source"] = pending_source

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        total = d.pop("total")

        def _parse_pending_first_signin(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        pending_first_signin = _parse_pending_first_signin(d.pop("pending_first_signin", UNSET))

        pending_source = cast(Literal["not_available"] | Unset, d.pop("pending_source", UNSET))
        if pending_source != "not_available" and not isinstance(pending_source, Unset):
            raise ValueError(
                f"pending_source must match const 'not_available', got '{pending_source}'"
            )

        overview_members_read = cls(
            total=total,
            pending_first_signin=pending_first_signin,
            pending_source=pending_source,
        )

        overview_members_read.additional_properties = d
        return overview_members_read

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
