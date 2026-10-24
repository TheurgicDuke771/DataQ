from enum import StrEnum


class SignupAllowlistReadGrants(StrEnum):
    SIGN_IN = "sign_in"
    SIGN_IN_AND_ADMIN_ROLE = "sign_in_and_admin_role"

    def __str__(self) -> str:
        return str(self.value)
