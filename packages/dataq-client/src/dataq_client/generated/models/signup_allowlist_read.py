from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.signup_allowlist_read_grants import SignupAllowlistReadGrants
from ..models.signup_allowlist_read_kind import SignupAllowlistReadKind

T = TypeVar("T", bound="SignupAllowlistRead")


@_attrs_define
class SignupAllowlistRead:
    """One deployment allowlist, as configured — identifiers only, never a secret.

    Attributes:
        active (bool):
        grants (SignupAllowlistReadGrants):
        kind (SignupAllowlistReadKind):
        values (list[str]):
        variable (str):
    """

    active: bool
    grants: SignupAllowlistReadGrants
    kind: SignupAllowlistReadKind
    values: list[str]
    variable: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        active = self.active

        grants = self.grants.value

        kind = self.kind.value

        values = self.values

        variable = self.variable

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "active": active,
                "grants": grants,
                "kind": kind,
                "values": values,
                "variable": variable,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        active = d.pop("active")

        grants = SignupAllowlistReadGrants(d.pop("grants"))

        kind = SignupAllowlistReadKind(d.pop("kind"))

        values = cast(list[str], d.pop("values"))

        variable = d.pop("variable")

        signup_allowlist_read = cls(
            active=active,
            grants=grants,
            kind=kind,
            values=values,
            variable=variable,
        )

        signup_allowlist_read.additional_properties = d
        return signup_allowlist_read

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
