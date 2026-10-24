from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.workspace_config_read_oidc_default_role import WorkspaceConfigReadOidcDefaultRole
from ..models.workspace_config_read_otp_default_role import WorkspaceConfigReadOtpDefaultRole
from ..models.workspace_config_read_sign_in_mode import WorkspaceConfigReadSignInMode

if TYPE_CHECKING:
    from ..models.retention_setting_read import RetentionSettingRead
    from ..models.signup_allowlist_read import SignupAllowlistRead


T = TypeVar("T", bound="WorkspaceConfigRead")


@_attrs_define
class WorkspaceConfigRead:
    """Deployment-set values an admin can read in the app but only change by
    editing the deployment's configuration and restarting.

        Attributes:
            dev_bypass_active (bool):
            idp_decides_signup (bool):
            oidc_default_role (WorkspaceConfigReadOidcDefaultRole):
            otp_default_role (WorkspaceConfigReadOtpDefaultRole):
            retention (list[RetentionSettingRead]):
            sign_in_mode (WorkspaceConfigReadSignInMode):
            signup_allowlists (list[SignupAllowlistRead]):
    """

    dev_bypass_active: bool
    idp_decides_signup: bool
    oidc_default_role: WorkspaceConfigReadOidcDefaultRole
    otp_default_role: WorkspaceConfigReadOtpDefaultRole
    retention: list[RetentionSettingRead]
    sign_in_mode: WorkspaceConfigReadSignInMode
    signup_allowlists: list[SignupAllowlistRead]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        dev_bypass_active = self.dev_bypass_active

        idp_decides_signup = self.idp_decides_signup

        oidc_default_role = self.oidc_default_role.value

        otp_default_role = self.otp_default_role.value

        retention = []
        for retention_item_data in self.retention:
            retention_item = retention_item_data.to_dict()
            retention.append(retention_item)

        sign_in_mode = self.sign_in_mode.value

        signup_allowlists = []
        for signup_allowlists_item_data in self.signup_allowlists:
            signup_allowlists_item = signup_allowlists_item_data.to_dict()
            signup_allowlists.append(signup_allowlists_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "dev_bypass_active": dev_bypass_active,
                "idp_decides_signup": idp_decides_signup,
                "oidc_default_role": oidc_default_role,
                "otp_default_role": otp_default_role,
                "retention": retention,
                "sign_in_mode": sign_in_mode,
                "signup_allowlists": signup_allowlists,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.retention_setting_read import RetentionSettingRead
        from ..models.signup_allowlist_read import SignupAllowlistRead

        d = dict(src_dict)
        dev_bypass_active = d.pop("dev_bypass_active")

        idp_decides_signup = d.pop("idp_decides_signup")

        oidc_default_role = WorkspaceConfigReadOidcDefaultRole(d.pop("oidc_default_role"))

        otp_default_role = WorkspaceConfigReadOtpDefaultRole(d.pop("otp_default_role"))

        retention = []
        _retention = d.pop("retention")
        for retention_item_data in _retention:
            retention_item = RetentionSettingRead.from_dict(retention_item_data)

            retention.append(retention_item)

        sign_in_mode = WorkspaceConfigReadSignInMode(d.pop("sign_in_mode"))

        signup_allowlists = []
        _signup_allowlists = d.pop("signup_allowlists")
        for signup_allowlists_item_data in _signup_allowlists:
            signup_allowlists_item = SignupAllowlistRead.from_dict(signup_allowlists_item_data)

            signup_allowlists.append(signup_allowlists_item)

        workspace_config_read = cls(
            dev_bypass_active=dev_bypass_active,
            idp_decides_signup=idp_decides_signup,
            oidc_default_role=oidc_default_role,
            otp_default_role=otp_default_role,
            retention=retention,
            sign_in_mode=sign_in_mode,
            signup_allowlists=signup_allowlists,
        )

        workspace_config_read.additional_properties = d
        return workspace_config_read

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
