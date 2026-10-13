from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.coverage_settings_read_setting import CoverageSettingsReadSetting
    from ..models.effective_coverage_read import EffectiveCoverageRead


T = TypeVar("T", bound="CoverageSettingsRead")


@_attrs_define
class CoverageSettingsRead:
    """The workspace's sensitivity for automatic baselines. `setting` is exactly what is
    stored (a group it omits falls to the built-in default); `effective` is the result for a
    table with no override of its own. `is_default` means no row is stored. A change applies
    to every inheriting automatic check on its next run.

        Attributes:
            defaults (EffectiveCoverageRead): What automatic baselines run on once the hierarchy is applied: asset override,
                then workspace setting, then the built-in default.
            effective (EffectiveCoverageRead): What automatic baselines run on once the hierarchy is applied: asset
                override,
                then workspace setting, then the built-in default.
            is_default (bool):
            setting (CoverageSettingsReadSetting):
            updated_at (datetime.datetime | None):
            updated_by (None | str):
    """

    defaults: EffectiveCoverageRead
    effective: EffectiveCoverageRead
    is_default: bool
    setting: CoverageSettingsReadSetting
    updated_at: datetime.datetime | None
    updated_by: None | str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        defaults = self.defaults.to_dict()

        effective = self.effective.to_dict()

        is_default = self.is_default

        setting = self.setting.to_dict()

        updated_at: None | str
        if isinstance(self.updated_at, datetime.datetime):
            updated_at = self.updated_at.isoformat()
        else:
            updated_at = self.updated_at

        updated_by: None | str
        updated_by = self.updated_by

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "defaults": defaults,
                "effective": effective,
                "is_default": is_default,
                "setting": setting,
                "updated_at": updated_at,
                "updated_by": updated_by,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.coverage_settings_read_setting import (
            CoverageSettingsReadSetting,
        )
        from ..models.effective_coverage_read import EffectiveCoverageRead

        d = dict(src_dict)
        defaults = EffectiveCoverageRead.from_dict(d.pop("defaults"))

        effective = EffectiveCoverageRead.from_dict(d.pop("effective"))

        is_default = d.pop("is_default")

        setting = CoverageSettingsReadSetting.from_dict(d.pop("setting"))

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

        coverage_settings_read = cls(
            defaults=defaults,
            effective=effective,
            is_default=is_default,
            setting=setting,
            updated_at=updated_at,
            updated_by=updated_by,
        )

        coverage_settings_read.additional_properties = d
        return coverage_settings_read

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
