from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.detected_classification_read_effect import DetectedClassificationReadEffect

T = TypeVar("T", bound="DetectedClassificationRead")


@_attrs_define
class DetectedClassificationRead:
    """A column the profiler found PII-shaped VALUES in (ADR 0049). `effect` is what it does
    now: `masks` (sensitive on every read path, unless a suite names the column as its own
    `identifier_column`), `overridden_by_tag` (the column's own warehouse tag decides instead,
    either way), `dismissed` (a workspace-Admin act). A dismissed row keeps `last_detected_at`
    moving, so "still detected" stays visible. `detector` is a label (`email` or `encoded`
    today) and not a closed enum on the wire, so a new detector never breaks this page.
    `evidence` is deliberately not exposed: it is counts only, and the detector says what fired.

        Attributes:
            column (str):
            detector (str):
            dismissed (bool):
            dismissed_at (datetime.datetime | None):
            effect (DetectedClassificationReadEffect):
            first_detected_at (datetime.datetime):
            last_detected_at (datetime.datetime):
    """

    column: str
    detector: str
    dismissed: bool
    dismissed_at: datetime.datetime | None
    effect: DetectedClassificationReadEffect
    first_detected_at: datetime.datetime
    last_detected_at: datetime.datetime
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        column = self.column

        detector = self.detector

        dismissed = self.dismissed

        dismissed_at: None | str
        if isinstance(self.dismissed_at, datetime.datetime):
            dismissed_at = self.dismissed_at.isoformat()
        else:
            dismissed_at = self.dismissed_at

        effect = self.effect.value

        first_detected_at = self.first_detected_at.isoformat()

        last_detected_at = self.last_detected_at.isoformat()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "column": column,
                "detector": detector,
                "dismissed": dismissed,
                "dismissed_at": dismissed_at,
                "effect": effect,
                "first_detected_at": first_detected_at,
                "last_detected_at": last_detected_at,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        column = d.pop("column")

        detector = d.pop("detector")

        dismissed = d.pop("dismissed")

        def _parse_dismissed_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                dismissed_at_type_0 = datetime.datetime.fromisoformat(data)

                return dismissed_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        dismissed_at = _parse_dismissed_at(d.pop("dismissed_at"))

        effect = DetectedClassificationReadEffect(d.pop("effect"))

        first_detected_at = datetime.datetime.fromisoformat(d.pop("first_detected_at"))

        last_detected_at = datetime.datetime.fromisoformat(d.pop("last_detected_at"))

        detected_classification_read = cls(
            column=column,
            detector=detector,
            dismissed=dismissed,
            dismissed_at=dismissed_at,
            effect=effect,
            first_detected_at=first_detected_at,
            last_detected_at=last_detected_at,
        )

        detected_classification_read.additional_properties = d
        return detected_classification_read

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
