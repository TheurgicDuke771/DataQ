from enum import StrEnum


class CredentialHealthReadStatus(StrEnum):
    FAILING = "failing"
    HEALTHY = "healthy"
    UNKNOWN = "unknown"

    def __str__(self) -> str:
        return str(self.value)
