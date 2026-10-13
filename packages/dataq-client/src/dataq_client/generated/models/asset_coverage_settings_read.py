from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.asset_coverage_settings_read_override_type_0 import (
        AssetCoverageSettingsReadOverrideType0,
    )
    from ..models.effective_coverage_read import EffectiveCoverageRead


T = TypeVar("T", bound="AssetCoverageSettingsRead")


@_attrs_define
class AssetCoverageSettingsRead:
    """One table's sensitivity for its automatic baselines. `override` is what this table
    stores itself (`null` = none); `effective` is what its inheriting automatic checks run on,
    with the level each group came from. A check a person has edited no longer inherits and
    runs on its own stored values instead.

        Attributes:
            effective (EffectiveCoverageRead): What automatic baselines run on once the hierarchy is applied: asset
                override,
                then workspace setting, then the built-in default.
            override (AssetCoverageSettingsReadOverrideType0 | None):
    """

    effective: EffectiveCoverageRead
    override: AssetCoverageSettingsReadOverrideType0 | None
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.asset_coverage_settings_read_override_type_0 import (
            AssetCoverageSettingsReadOverrideType0,
        )

        effective = self.effective.to_dict()

        override: dict[str, Any] | None
        if isinstance(self.override, AssetCoverageSettingsReadOverrideType0):
            override = self.override.to_dict()
        else:
            override = self.override

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "effective": effective,
                "override": override,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.asset_coverage_settings_read_override_type_0 import (
            AssetCoverageSettingsReadOverrideType0,
        )
        from ..models.effective_coverage_read import EffectiveCoverageRead

        d = dict(src_dict)
        effective = EffectiveCoverageRead.from_dict(d.pop("effective"))

        def _parse_override(data: object) -> AssetCoverageSettingsReadOverrideType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                override_type_0 = AssetCoverageSettingsReadOverrideType0.from_dict(data)

                return override_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(AssetCoverageSettingsReadOverrideType0 | None, data)

        override = _parse_override(d.pop("override"))

        asset_coverage_settings_read = cls(
            effective=effective,
            override=override,
        )

        asset_coverage_settings_read.additional_properties = d
        return asset_coverage_settings_read

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
