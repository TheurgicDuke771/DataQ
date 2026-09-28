from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.offboard_preview_read_membership_state import OffboardPreviewReadMembershipState

if TYPE_CHECKING:
    from ..models.owned_suite_read import OwnedSuiteRead


T = TypeVar("T", bound="OffboardPreviewRead")


@_attrs_define
class OffboardPreviewRead:
    """
    Attributes:
        display_name (None | str):
        email (str):
        is_last_admin (bool):
        is_self (bool):
        live_session_count (int):
        membership_id (None | UUID):
        membership_note (None | str):
        membership_state (OffboardPreviewReadMembershipState):
        open_api_key_count (int):
        owned_suites (list[OwnedSuiteRead]):
        role (str):
        still_admitted_by (list[str]):
        user_id (UUID):
    """

    display_name: None | str
    email: str
    is_last_admin: bool
    is_self: bool
    live_session_count: int
    membership_id: None | UUID
    membership_note: None | str
    membership_state: OffboardPreviewReadMembershipState
    open_api_key_count: int
    owned_suites: list[OwnedSuiteRead]
    role: str
    still_admitted_by: list[str]
    user_id: UUID
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        display_name: None | str
        display_name = self.display_name

        email = self.email

        is_last_admin = self.is_last_admin

        is_self = self.is_self

        live_session_count = self.live_session_count

        membership_id: None | str
        if isinstance(self.membership_id, UUID):
            membership_id = str(self.membership_id)
        else:
            membership_id = self.membership_id

        membership_note: None | str
        membership_note = self.membership_note

        membership_state = self.membership_state.value

        open_api_key_count = self.open_api_key_count

        owned_suites = []
        for owned_suites_item_data in self.owned_suites:
            owned_suites_item = owned_suites_item_data.to_dict()
            owned_suites.append(owned_suites_item)

        role = self.role

        still_admitted_by = self.still_admitted_by

        user_id = str(self.user_id)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "display_name": display_name,
                "email": email,
                "is_last_admin": is_last_admin,
                "is_self": is_self,
                "live_session_count": live_session_count,
                "membership_id": membership_id,
                "membership_note": membership_note,
                "membership_state": membership_state,
                "open_api_key_count": open_api_key_count,
                "owned_suites": owned_suites,
                "role": role,
                "still_admitted_by": still_admitted_by,
                "user_id": user_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.owned_suite_read import OwnedSuiteRead

        d = dict(src_dict)

        def _parse_display_name(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        display_name = _parse_display_name(d.pop("display_name"))

        email = d.pop("email")

        is_last_admin = d.pop("is_last_admin")

        is_self = d.pop("is_self")

        live_session_count = d.pop("live_session_count")

        def _parse_membership_id(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                membership_id_type_0 = UUID(data)

                return membership_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | UUID, data)

        membership_id = _parse_membership_id(d.pop("membership_id"))

        def _parse_membership_note(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        membership_note = _parse_membership_note(d.pop("membership_note"))

        membership_state = OffboardPreviewReadMembershipState(d.pop("membership_state"))

        open_api_key_count = d.pop("open_api_key_count")

        owned_suites = []
        _owned_suites = d.pop("owned_suites")
        for owned_suites_item_data in _owned_suites:
            owned_suites_item = OwnedSuiteRead.from_dict(owned_suites_item_data)

            owned_suites.append(owned_suites_item)

        role = d.pop("role")

        still_admitted_by = cast(list[str], d.pop("still_admitted_by"))

        user_id = UUID(d.pop("user_id"))

        offboard_preview_read = cls(
            display_name=display_name,
            email=email,
            is_last_admin=is_last_admin,
            is_self=is_self,
            live_session_count=live_session_count,
            membership_id=membership_id,
            membership_note=membership_note,
            membership_state=membership_state,
            open_api_key_count=open_api_key_count,
            owned_suites=owned_suites,
            role=role,
            still_admitted_by=still_admitted_by,
            user_id=user_id,
        )

        offboard_preview_read.additional_properties = d
        return offboard_preview_read

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
