from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.baseline_shape_write import BaselineShapeWrite
    from ..models.coverage_settings_write_sensitivity_type_0 import (
        CoverageSettingsWriteSensitivityType0,
    )


T = TypeVar("T", bound="CoverageSettingsWrite")


@_attrs_define
class CoverageSettingsWrite:
    """A level sets a group whole or leaves it to the level below: any of the three
    baselines' thresholds, and the shared baseline shape.

        Attributes:
            baseline (BaselineShapeWrite | None | Unset):
            sensitivity (CoverageSettingsWriteSensitivityType0 | None | Unset):
    """

    baseline: BaselineShapeWrite | None | Unset = UNSET
    sensitivity: CoverageSettingsWriteSensitivityType0 | None | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.baseline_shape_write import BaselineShapeWrite
        from ..models.coverage_settings_write_sensitivity_type_0 import (
            CoverageSettingsWriteSensitivityType0,
        )

        baseline: dict[str, Any] | None | Unset
        if isinstance(self.baseline, Unset):
            baseline = UNSET
        elif isinstance(self.baseline, BaselineShapeWrite):
            baseline = self.baseline.to_dict()
        else:
            baseline = self.baseline

        sensitivity: dict[str, Any] | None | Unset
        if isinstance(self.sensitivity, Unset):
            sensitivity = UNSET
        elif isinstance(self.sensitivity, CoverageSettingsWriteSensitivityType0):
            sensitivity = self.sensitivity.to_dict()
        else:
            sensitivity = self.sensitivity

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if baseline is not UNSET:
            field_dict["baseline"] = baseline
        if sensitivity is not UNSET:
            field_dict["sensitivity"] = sensitivity

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.baseline_shape_write import BaselineShapeWrite
        from ..models.coverage_settings_write_sensitivity_type_0 import (
            CoverageSettingsWriteSensitivityType0,
        )

        d = dict(src_dict)

        def _parse_baseline(data: object) -> BaselineShapeWrite | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                baseline_type_0 = BaselineShapeWrite.from_dict(data)

                return baseline_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(BaselineShapeWrite | None | Unset, data)

        baseline = _parse_baseline(d.pop("baseline", UNSET))

        def _parse_sensitivity(
            data: object,
        ) -> CoverageSettingsWriteSensitivityType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                sensitivity_type_0 = CoverageSettingsWriteSensitivityType0.from_dict(data)

                return sensitivity_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(CoverageSettingsWriteSensitivityType0 | None | Unset, data)

        sensitivity = _parse_sensitivity(d.pop("sensitivity", UNSET))

        coverage_settings_write = cls(
            baseline=baseline,
            sensitivity=sensitivity,
        )

        return coverage_settings_write
