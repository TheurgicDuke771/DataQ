from enum import StrEnum


class MemberReadSource(StrEnum):
    ADMIN = "admin"
    AUTO_IMPORT = "auto_import"
    ENV = "env"

    def __str__(self) -> str:
        return str(self.value)
