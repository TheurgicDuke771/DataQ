from enum import StrEnum


class AdminCredentialHealthReadStatus(StrEnum):
    FAILING = "failing"
    HEALTHY = "healthy"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"

    def __str__(self) -> str:
        return str(self.value)
