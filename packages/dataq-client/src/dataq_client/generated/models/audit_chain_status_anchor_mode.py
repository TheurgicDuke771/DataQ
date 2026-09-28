from enum import StrEnum


class AuditChainStatusAnchorMode(StrEnum):
    NONE = "none"
    WEBHOOK = "webhook"

    def __str__(self) -> str:
        return str(self.value)
