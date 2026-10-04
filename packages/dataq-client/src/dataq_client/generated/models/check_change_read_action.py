from enum import StrEnum


class CheckChangeReadAction(StrEnum):
    CREATE = "create"
    DELETE = "delete"
    UNCHANGED = "unchanged"
    UPDATE = "update"

    def __str__(self) -> str:
        return str(self.value)
