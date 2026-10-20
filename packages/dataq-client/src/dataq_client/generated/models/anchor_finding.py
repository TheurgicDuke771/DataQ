from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.anchor_finding_match import AnchorFindingMatch

T = TypeVar("T", bound="AnchorFinding")


@_attrs_define
class AnchorFinding:
    """
    Attributes:
        as_of (datetime.datetime | None):
        head_hash (None | str):
        key (str):
        label (None | str):
        match (AnchorFindingMatch):
    """

    as_of: datetime.datetime | None
    head_hash: None | str
    key: str
    label: None | str
    match: AnchorFindingMatch
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        as_of: None | str
        if isinstance(self.as_of, datetime.datetime):
            as_of = self.as_of.isoformat()
        else:
            as_of = self.as_of

        head_hash: None | str
        head_hash = self.head_hash

        key = self.key

        label: None | str
        label = self.label

        match = self.match.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "as_of": as_of,
                "head_hash": head_hash,
                "key": key,
                "label": label,
                "match": match,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)

        def _parse_as_of(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                as_of_type_0 = datetime.datetime.fromisoformat(data)

                return as_of_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        as_of = _parse_as_of(d.pop("as_of"))

        def _parse_head_hash(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        head_hash = _parse_head_hash(d.pop("head_hash"))

        key = d.pop("key")

        def _parse_label(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        label = _parse_label(d.pop("label"))

        match = AnchorFindingMatch(d.pop("match"))

        anchor_finding = cls(
            as_of=as_of,
            head_hash=head_hash,
            key=key,
            label=label,
            match=match,
        )

        anchor_finding.additional_properties = d
        return anchor_finding

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
