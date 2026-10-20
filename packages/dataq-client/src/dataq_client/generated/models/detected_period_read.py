from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.detected_period_read_kind import DetectedPeriodReadKind

if TYPE_CHECKING:
    from ..models.declared_ref_read import DeclaredRefRead
    from ..models.detected_occurrence_read import DetectedOccurrenceRead
    from ..models.detected_range_read import DetectedRangeRead


T = TypeVar("T", bound="DetectedPeriodRead")


@_attrs_define
class DetectedPeriodRead:
    """
    Attributes:
        declared (list[DeclaredRefRead]):
        end (datetime.date):
        kind (DetectedPeriodReadKind):
        occurrences (list[DetectedOccurrenceRead]):
        peak_ratio (float):
        range_ (DetectedRangeRead | None):
        start (datetime.date):
        years_seen (int):
    """

    declared: list[DeclaredRefRead]
    end: datetime.date
    kind: DetectedPeriodReadKind
    occurrences: list[DetectedOccurrenceRead]
    peak_ratio: float
    range_: DetectedRangeRead | None
    start: datetime.date
    years_seen: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.detected_range_read import DetectedRangeRead

        declared = []
        for declared_item_data in self.declared:
            declared_item = declared_item_data.to_dict()
            declared.append(declared_item)

        end = self.end.isoformat()

        kind = self.kind.value

        occurrences = []
        for occurrences_item_data in self.occurrences:
            occurrences_item = occurrences_item_data.to_dict()
            occurrences.append(occurrences_item)

        peak_ratio = self.peak_ratio

        range_: dict[str, Any] | None
        if isinstance(self.range_, DetectedRangeRead):
            range_ = self.range_.to_dict()
        else:
            range_ = self.range_

        start = self.start.isoformat()

        years_seen = self.years_seen

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "declared": declared,
                "end": end,
                "kind": kind,
                "occurrences": occurrences,
                "peak_ratio": peak_ratio,
                "range": range_,
                "start": start,
                "years_seen": years_seen,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.declared_ref_read import DeclaredRefRead
        from ..models.detected_occurrence_read import DetectedOccurrenceRead
        from ..models.detected_range_read import DetectedRangeRead

        d = dict(src_dict)
        declared = []
        _declared = d.pop("declared")
        for declared_item_data in _declared:
            declared_item = DeclaredRefRead.from_dict(declared_item_data)

            declared.append(declared_item)

        end = datetime.date.fromisoformat(d.pop("end"))

        kind = DetectedPeriodReadKind(d.pop("kind"))

        occurrences = []
        _occurrences = d.pop("occurrences")
        for occurrences_item_data in _occurrences:
            occurrences_item = DetectedOccurrenceRead.from_dict(occurrences_item_data)

            occurrences.append(occurrences_item)

        peak_ratio = d.pop("peak_ratio")

        def _parse_range_(data: object) -> DetectedRangeRead | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                range_type_0 = DetectedRangeRead.from_dict(data)

                return range_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(DetectedRangeRead | None, data)

        range_ = _parse_range_(d.pop("range"))

        start = datetime.date.fromisoformat(d.pop("start"))

        years_seen = d.pop("years_seen")

        detected_period_read = cls(
            declared=declared,
            end=end,
            kind=kind,
            occurrences=occurrences,
            peak_ratio=peak_ratio,
            range_=range_,
            start=start,
            years_seen=years_seen,
        )

        detected_period_read.additional_properties = d
        return detected_period_read

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
