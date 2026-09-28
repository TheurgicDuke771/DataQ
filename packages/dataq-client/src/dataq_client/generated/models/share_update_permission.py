from enum import StrEnum


class ShareUpdatePermission(StrEnum):
    EDIT = "edit"
    VIEW = "view"

    def __str__(self) -> str:
        return str(self.value)
