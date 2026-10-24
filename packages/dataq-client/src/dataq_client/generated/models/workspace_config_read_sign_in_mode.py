from enum import StrEnum


class WorkspaceConfigReadSignInMode(StrEnum):
    AZURE_AD = "azure_ad"
    DEV_BYPASS = "dev_bypass"
    NONE = "none"
    OIDC = "oidc"
    OTP = "otp"

    def __str__(self) -> str:
        return str(self.value)
