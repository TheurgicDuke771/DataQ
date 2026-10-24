from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="SearchHitRead")


@_attrs_define
class SearchHitRead:
    """One match. `label` is the entity's name (a run is labelled by its suite); `href` is the
    SPA path that opens it for THIS caller — a check the caller may only view opens its suite,
    a connection opens its own page only for a workspace admin. `when` is set on a run (when it
    started, or was queued if it never started).

        Attributes:
            href (str):
            id (UUID):
            label (str):
            sublabel (None | str | Unset):
            when (datetime.datetime | None | Unset):
    """

    href: str
    id: UUID
    label: str
    sublabel: None | str | Unset = UNSET
    when: datetime.datetime | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        href = self.href

        id = str(self.id)

        label = self.label

        sublabel: None | str | Unset
        if isinstance(self.sublabel, Unset):
            sublabel = UNSET
        else:
            sublabel = self.sublabel

        when: None | str | Unset
        if isinstance(self.when, Unset):
            when = UNSET
        elif isinstance(self.when, datetime.datetime):
            when = self.when.isoformat()
        else:
            when = self.when

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "href": href,
                "id": id,
                "label": label,
            }
        )
        if sublabel is not UNSET:
            field_dict["sublabel"] = sublabel
        if when is not UNSET:
            field_dict["when"] = when

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        href = d.pop("href")

        id = UUID(d.pop("id"))

        label = d.pop("label")

        def _parse_sublabel(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        sublabel = _parse_sublabel(d.pop("sublabel", UNSET))

        def _parse_when(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                when_type_0 = datetime.datetime.fromisoformat(data)

                return when_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        when = _parse_when(d.pop("when", UNSET))

        search_hit_read = cls(
            href=href,
            id=id,
            label=label,
            sublabel=sublabel,
            when=when,
        )

        search_hit_read.additional_properties = d
        return search_hit_read

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
