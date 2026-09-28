from enum import StrEnum


class AdminCredentialHealthReadStatus(StrEnum):
    FAILING = "failing"
    HEALTHY = "healthy"
    UNKNOWN = "unknown"

    def __str__(self) -> str:
        return str(self.value)
