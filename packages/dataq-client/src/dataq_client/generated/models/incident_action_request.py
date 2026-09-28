from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="IncidentActionRequest")


@_attrs_define
class IncidentActionRequest:
    """Optional note on an ack / resolve. NUL bytes are rejected by ``ApiModel``;
    the length cap keeps a hostile note off the unbounded Text column.

        Attributes:
            note (None | str | Unset):
    """

    note: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        note: None | str | Unset
        if isinstance(self.note, Unset):
            note = UNSET
        else:
            note = self.note

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if note is not UNSET:
            field_dict["note"] = note

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)

        def _parse_note(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        note = _parse_note(d.pop("note", UNSET))

        incident_action_request = cls(
            note=note,
        )

        return incident_action_request
