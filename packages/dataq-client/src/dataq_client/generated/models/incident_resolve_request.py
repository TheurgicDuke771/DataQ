from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from typing_extensions import Self

from ..models.incident_resolve_request_resolution_type_0 import (
    IncidentResolveRequestResolutionType0,
)
from ..types import UNSET, Unset

T = TypeVar("T", bound="IncidentResolveRequest")


@_attrs_define
class IncidentResolveRequest:
    """
    Attributes:
        note (None | str | Unset):
        resolution (IncidentResolveRequestResolutionType0 | None | Unset):
    """

    note: None | str | Unset = UNSET
    resolution: IncidentResolveRequestResolutionType0 | None | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        note: None | str | Unset
        if isinstance(self.note, Unset):
            note = UNSET
        else:
            note = self.note

        resolution: None | str | Unset
        if isinstance(self.resolution, Unset):
            resolution = UNSET
        elif isinstance(self.resolution, IncidentResolveRequestResolutionType0):
            resolution = self.resolution.value
        else:
            resolution = self.resolution

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if note is not UNSET:
            field_dict["note"] = note
        if resolution is not UNSET:
            field_dict["resolution"] = resolution

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

        def _parse_resolution(data: object) -> IncidentResolveRequestResolutionType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                resolution_type_0 = IncidentResolveRequestResolutionType0(data)

                return resolution_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(IncidentResolveRequestResolutionType0 | None | Unset, data)

        resolution = _parse_resolution(d.pop("resolution", UNSET))

        incident_resolve_request = cls(
            note=note,
            resolution=resolution,
        )

        return incident_resolve_request
