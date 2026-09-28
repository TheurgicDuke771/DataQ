from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.suite_read_column_policy_type_0 import SuiteReadColumnPolicyType0
    from ..models.suite_read_target_type_0 import SuiteReadTargetType0


T = TypeVar("T", bound="SuiteRead")


@_attrs_define
class SuiteRead:
    """
    Attributes:
        connection_id (UUID):
        created_by (None | UUID):
        description (None | str):
        id (UUID):
        name (str):
        target (None | SuiteReadTargetType0):
        asset_id (None | Unset | UUID):
        column_policy (None | SuiteReadColumnPolicyType0 | Unset):
        my_permission (None | str | Unset):
    """

    connection_id: UUID
    created_by: None | UUID
    description: None | str
    id: UUID
    name: str
    target: None | SuiteReadTargetType0
    asset_id: None | Unset | UUID = UNSET
    column_policy: None | SuiteReadColumnPolicyType0 | Unset = UNSET
    my_permission: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.suite_read_column_policy_type_0 import (
            SuiteReadColumnPolicyType0,
        )
        from ..models.suite_read_target_type_0 import SuiteReadTargetType0

        connection_id = str(self.connection_id)

        created_by: None | str
        if isinstance(self.created_by, UUID):
            created_by = str(self.created_by)
        else:
            created_by = self.created_by

        description: None | str
        description = self.description

        id = str(self.id)

        name = self.name

        target: dict[str, Any] | None
        if isinstance(self.target, SuiteReadTargetType0):
            target = self.target.to_dict()
        else:
            target = self.target

        asset_id: None | str | Unset
        if isinstance(self.asset_id, Unset):
            asset_id = UNSET
        elif isinstance(self.asset_id, UUID):
            asset_id = str(self.asset_id)
        else:
            asset_id = self.asset_id

        column_policy: dict[str, Any] | None | Unset
        if isinstance(self.column_policy, Unset):
            column_policy = UNSET
        elif isinstance(self.column_policy, SuiteReadColumnPolicyType0):
            column_policy = self.column_policy.to_dict()
        else:
            column_policy = self.column_policy

        my_permission: None | str | Unset
        if isinstance(self.my_permission, Unset):
            my_permission = UNSET
        else:
            my_permission = self.my_permission

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "connection_id": connection_id,
                "created_by": created_by,
                "description": description,
                "id": id,
                "name": name,
                "target": target,
            }
        )
        if asset_id is not UNSET:
            field_dict["asset_id"] = asset_id
        if column_policy is not UNSET:
            field_dict["column_policy"] = column_policy
        if my_permission is not UNSET:
            field_dict["my_permission"] = my_permission

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.suite_read_column_policy_type_0 import (
            SuiteReadColumnPolicyType0,
        )
        from ..models.suite_read_target_type_0 import SuiteReadTargetType0

        d = dict(src_dict)
        connection_id = UUID(d.pop("connection_id"))

        def _parse_created_by(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                created_by_type_0 = UUID(data)

                return created_by_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | UUID, data)

        created_by = _parse_created_by(d.pop("created_by"))

        def _parse_description(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        description = _parse_description(d.pop("description"))

        id = UUID(d.pop("id"))

        name = d.pop("name")

        def _parse_target(data: object) -> None | SuiteReadTargetType0:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                target_type_0 = SuiteReadTargetType0.from_dict(data)

                return target_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | SuiteReadTargetType0, data)

        target = _parse_target(d.pop("target"))

        def _parse_asset_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                asset_id_type_0 = UUID(data)

                return asset_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | Unset | UUID, data)

        asset_id = _parse_asset_id(d.pop("asset_id", UNSET))

        def _parse_column_policy(data: object) -> None | SuiteReadColumnPolicyType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                column_policy_type_0 = SuiteReadColumnPolicyType0.from_dict(data)

                return column_policy_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | SuiteReadColumnPolicyType0 | Unset, data)

        column_policy = _parse_column_policy(d.pop("column_policy", UNSET))

        def _parse_my_permission(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        my_permission = _parse_my_permission(d.pop("my_permission", UNSET))

        suite_read = cls(
            connection_id=connection_id,
            created_by=created_by,
            description=description,
            id=id,
            name=name,
            target=target,
            asset_id=asset_id,
            column_policy=column_policy,
            my_permission=my_permission,
        )

        suite_read.additional_properties = d
        return suite_read

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
