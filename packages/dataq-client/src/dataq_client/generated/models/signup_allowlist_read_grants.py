from enum import StrEnum


class SignupAllowlistReadGrants(StrEnum):
    ADMIN_ROLE = "admin_role"
    SIGN_IN = "sign_in"

    def __str__(self) -> str:
        return str(self.value)
