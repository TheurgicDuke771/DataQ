from enum import StrEnum


class WorkspaceConfigReadOidcDefaultRole(StrEnum):
    MEMBER = "member"
    VIEWER = "viewer"

    def __str__(self) -> str:
        return str(self.value)
