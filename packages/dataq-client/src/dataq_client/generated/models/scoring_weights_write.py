from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from typing_extensions import Self

T = TypeVar("T", bound="ScoringWeightsWrite")


@_attrs_define
class ScoringWeightsWrite:
    """
    Attributes:
        critical (float):
        fail (float):
        warn (float):
    """

    critical: float
    fail: float
    warn: float

    def to_dict(self) -> dict[str, Any]:
        critical = self.critical

        fail = self.fail

        warn = self.warn

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "critical": critical,
                "fail": fail,
                "warn": warn,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        critical = d.pop("critical")

        fail = d.pop("fail")

        warn = d.pop("warn")

        scoring_weights_write = cls(
            critical=critical,
            fail=fail,
            warn=warn,
        )

        return scoring_weights_write
