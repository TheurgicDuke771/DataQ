from enum import StrEnum


class AuditChainStatusStatus(StrEnum):
    BROKEN = "broken"
    EMPTY = "empty"
    OK = "ok"

    def __str__(self) -> str:
        return str(self.value)
