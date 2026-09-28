from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.scoring_weights_read_defaults import ScoringWeightsReadDefaults


T = TypeVar("T", bound="ScoringWeightsRead")


@_attrs_define
class ScoringWeightsRead:
    """The penalties applied per severity tier when a health score is computed.
    `pass` is always 0. `is_default` means no row is stored and the ADR 0005
    defaults apply. Scores are computed on read, so a change recolours every
    score at once, past and present.

        Attributes:
            critical (float):
            defaults (ScoringWeightsReadDefaults):
            fail (float):
            is_default (bool):
            updated_at (datetime.datetime | None):
            updated_by (None | str):
            warn (float):
    """

    critical: float
    defaults: ScoringWeightsReadDefaults
    fail: float
    is_default: bool
    updated_at: datetime.datetime | None
    updated_by: None | str
    warn: float
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        critical = self.critical

        defaults = self.defaults.to_dict()

        fail = self.fail

        is_default = self.is_default

        updated_at: None | str
        if isinstance(self.updated_at, datetime.datetime):
            updated_at = self.updated_at.isoformat()
        else:
            updated_at = self.updated_at

        updated_by: None | str
        updated_by = self.updated_by

        warn = self.warn

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "critical": critical,
                "defaults": defaults,
                "fail": fail,
                "is_default": is_default,
                "updated_at": updated_at,
                "updated_by": updated_by,
                "warn": warn,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.scoring_weights_read_defaults import (
            ScoringWeightsReadDefaults,
        )

        d = dict(src_dict)
        critical = d.pop("critical")

        defaults = ScoringWeightsReadDefaults.from_dict(d.pop("defaults"))

        fail = d.pop("fail")

        is_default = d.pop("is_default")

        def _parse_updated_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                updated_at_type_0 = datetime.datetime.fromisoformat(data)

                return updated_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        updated_at = _parse_updated_at(d.pop("updated_at"))

        def _parse_updated_by(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        updated_by = _parse_updated_by(d.pop("updated_by"))

        warn = d.pop("warn")

        scoring_weights_read = cls(
            critical=critical,
            defaults=defaults,
            fail=fail,
            is_default=is_default,
            updated_at=updated_at,
            updated_by=updated_by,
            warn=warn,
        )

        scoring_weights_read.additional_properties = d
        return scoring_weights_read

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
