from enum import StrEnum


class InventorySyncReadStatus(StrEnum):
    FAILING = "failing"
    NEVER_SYNCED = "never_synced"
    SYNCED = "synced"

    def __str__(self) -> str:
        return str(self.value)
