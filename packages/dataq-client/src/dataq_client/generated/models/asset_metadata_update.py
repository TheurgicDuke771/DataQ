from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.coverage_settings_write import CoverageSettingsWrite


T = TypeVar("T", bound="AssetMetadataUpdate")


@_attrs_define
class AssetMetadataUpdate:
    """Partial metadata update (workspace-Admin-only). Each field is optional; an
    explicit `null` clears it, an omitted field leaves it unchanged — the two are
    distinguished via `model_fields_set` at the route so `owner_user_id: null`
    means "unassign" rather than "leave as is".

        Attributes:
            auto_coverage_config (CoverageSettingsWrite | None | Unset):
            auto_coverage_excluded (bool | None | Unset):
            description (None | str | Unset):
            owner_user_id (None | Unset | UUID):
    """

    auto_coverage_config: CoverageSettingsWrite | None | Unset = UNSET
    auto_coverage_excluded: bool | None | Unset = UNSET
    description: None | str | Unset = UNSET
    owner_user_id: None | Unset | UUID = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.coverage_settings_write import CoverageSettingsWrite

        auto_coverage_config: dict[str, Any] | None | Unset
        if isinstance(self.auto_coverage_config, Unset):
            auto_coverage_config = UNSET
        elif isinstance(self.auto_coverage_config, CoverageSettingsWrite):
            auto_coverage_config = self.auto_coverage_config.to_dict()
        else:
            auto_coverage_config = self.auto_coverage_config

        auto_coverage_excluded: bool | None | Unset
        if isinstance(self.auto_coverage_excluded, Unset):
            auto_coverage_excluded = UNSET
        else:
            auto_coverage_excluded = self.auto_coverage_excluded

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        owner_user_id: None | str | Unset
        if isinstance(self.owner_user_id, Unset):
            owner_user_id = UNSET
        elif isinstance(self.owner_user_id, UUID):
            owner_user_id = str(self.owner_user_id)
        else:
            owner_user_id = self.owner_user_id

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if auto_coverage_config is not UNSET:
            field_dict["auto_coverage_config"] = auto_coverage_config
        if auto_coverage_excluded is not UNSET:
            field_dict["auto_coverage_excluded"] = auto_coverage_excluded
        if description is not UNSET:
            field_dict["description"] = description
        if owner_user_id is not UNSET:
            field_dict["owner_user_id"] = owner_user_id

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.coverage_settings_write import CoverageSettingsWrite

        d = dict(src_dict)

        def _parse_auto_coverage_config(data: object) -> CoverageSettingsWrite | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                auto_coverage_config_type_0 = CoverageSettingsWrite.from_dict(data)

                return auto_coverage_config_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(CoverageSettingsWrite | None | Unset, data)

        auto_coverage_config = _parse_auto_coverage_config(d.pop("auto_coverage_config", UNSET))

        def _parse_auto_coverage_excluded(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        auto_coverage_excluded = _parse_auto_coverage_excluded(
            d.pop("auto_coverage_excluded", UNSET)
        )

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_owner_user_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                owner_user_id_type_0 = UUID(data)

                return owner_user_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | Unset | UUID, data)

        owner_user_id = _parse_owner_user_id(d.pop("owner_user_id", UNSET))

        asset_metadata_update = cls(
            auto_coverage_config=auto_coverage_config,
            auto_coverage_excluded=auto_coverage_excluded,
            description=description,
            owner_user_id=owner_user_id,
        )

        return asset_metadata_update
