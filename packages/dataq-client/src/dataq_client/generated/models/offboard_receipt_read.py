from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.offboard_receipt_read_skipped_item import OffboardReceiptReadSkippedItem


T = TypeVar("T", bound="OffboardReceiptRead")


@_attrs_define
class OffboardReceiptRead:
    """
    Attributes:
        api_keys_revoked (int):
        email (str):
        membership_removed (bool):
        new_owner_user_id (None | UUID):
        role_demoted_from (None | str):
        sessions_revoked (int):
        skipped (list[OffboardReceiptReadSkippedItem]):
        still_admitted_by (list[str]):
        transferred_suite_ids (list[UUID]):
        user_id (UUID):
    """

    api_keys_revoked: int
    email: str
    membership_removed: bool
    new_owner_user_id: None | UUID
    role_demoted_from: None | str
    sessions_revoked: int
    skipped: list[OffboardReceiptReadSkippedItem]
    still_admitted_by: list[str]
    transferred_suite_ids: list[UUID]
    user_id: UUID
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        api_keys_revoked = self.api_keys_revoked

        email = self.email

        membership_removed = self.membership_removed

        new_owner_user_id: None | str
        if isinstance(self.new_owner_user_id, UUID):
            new_owner_user_id = str(self.new_owner_user_id)
        else:
            new_owner_user_id = self.new_owner_user_id

        role_demoted_from: None | str
        role_demoted_from = self.role_demoted_from

        sessions_revoked = self.sessions_revoked

        skipped = []
        for skipped_item_data in self.skipped:
            skipped_item = skipped_item_data.to_dict()
            skipped.append(skipped_item)

        still_admitted_by = self.still_admitted_by

        transferred_suite_ids = []
        for transferred_suite_ids_item_data in self.transferred_suite_ids:
            transferred_suite_ids_item = str(transferred_suite_ids_item_data)
            transferred_suite_ids.append(transferred_suite_ids_item)

        user_id = str(self.user_id)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "api_keys_revoked": api_keys_revoked,
                "email": email,
                "membership_removed": membership_removed,
                "new_owner_user_id": new_owner_user_id,
                "role_demoted_from": role_demoted_from,
                "sessions_revoked": sessions_revoked,
                "skipped": skipped,
                "still_admitted_by": still_admitted_by,
                "transferred_suite_ids": transferred_suite_ids,
                "user_id": user_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.offboard_receipt_read_skipped_item import (
            OffboardReceiptReadSkippedItem,
        )

        d = dict(src_dict)
        api_keys_revoked = d.pop("api_keys_revoked")

        email = d.pop("email")

        membership_removed = d.pop("membership_removed")

        def _parse_new_owner_user_id(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                new_owner_user_id_type_0 = UUID(data)

                return new_owner_user_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | UUID, data)

        new_owner_user_id = _parse_new_owner_user_id(d.pop("new_owner_user_id"))

        def _parse_role_demoted_from(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        role_demoted_from = _parse_role_demoted_from(d.pop("role_demoted_from"))

        sessions_revoked = d.pop("sessions_revoked")

        skipped = []
        _skipped = d.pop("skipped")
        for skipped_item_data in _skipped:
            skipped_item = OffboardReceiptReadSkippedItem.from_dict(skipped_item_data)

            skipped.append(skipped_item)

        still_admitted_by = cast(list[str], d.pop("still_admitted_by"))

        transferred_suite_ids = []
        _transferred_suite_ids = d.pop("transferred_suite_ids")
        for transferred_suite_ids_item_data in _transferred_suite_ids:
            transferred_suite_ids_item = UUID(transferred_suite_ids_item_data)

            transferred_suite_ids.append(transferred_suite_ids_item)

        user_id = UUID(d.pop("user_id"))

        offboard_receipt_read = cls(
            api_keys_revoked=api_keys_revoked,
            email=email,
            membership_removed=membership_removed,
            new_owner_user_id=new_owner_user_id,
            role_demoted_from=role_demoted_from,
            sessions_revoked=sessions_revoked,
            skipped=skipped,
            still_admitted_by=still_admitted_by,
            transferred_suite_ids=transferred_suite_ids,
            user_id=user_id,
        )

        offboard_receipt_read.additional_properties = d
        return offboard_receipt_read

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
