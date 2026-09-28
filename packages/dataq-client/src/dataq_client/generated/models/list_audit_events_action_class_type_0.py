from enum import StrEnum


class ListAuditEventsActionClassType0(StrEnum):
    ACCESS = "access"
    CONFIG = "config"

    def __str__(self) -> str:
        return str(self.value)
