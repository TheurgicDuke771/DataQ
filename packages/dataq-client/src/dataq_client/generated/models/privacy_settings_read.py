from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.privacy_settings_read_source import PrivacySettingsReadSource

T = TypeVar("T", bound="PrivacySettingsRead")


@_attrs_define
class PrivacySettingsRead:
    """`effective` is what every sample writer obeys (env floor OR the stored value);
    `stored` is only what the toggle last wrote. `env_forced` means the toggle can
    turn the mode on but not off.

        Attributes:
            effective (bool):
            env_forced (bool):
            source (PrivacySettingsReadSource):
            stored (bool):
            updated_at (datetime.datetime | None):
            updated_by (None | str):
    """

    effective: bool
    env_forced: bool
    source: PrivacySettingsReadSource
    stored: bool
    updated_at: datetime.datetime | None
    updated_by: None | str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        effective = self.effective

        env_forced = self.env_forced

        source = self.source.value

        stored = self.stored

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
                "effective": effective,
                "env_forced": env_forced,
                "source": source,
                "stored": stored,
                "updated_at": updated_at,
                "updated_by": updated_by,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        effective = d.pop("effective")

        env_forced = d.pop("env_forced")

        source = PrivacySettingsReadSource(d.pop("source"))

        stored = d.pop("stored")

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

        privacy_settings_read = cls(
            effective=effective,
            env_forced=env_forced,
            source=source,
            stored=stored,
            updated_at=updated_at,
            updated_by=updated_by,
        )

        privacy_settings_read.additional_properties = d
        return privacy_settings_read

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
