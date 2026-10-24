from enum import StrEnum


class SignupAllowlistReadKind(StrEnum):
    DOMAINS = "domains"
    EMAILS = "emails"

    def __str__(self) -> str:
        return str(self.value)
