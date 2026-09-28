from enum import StrEnum


class OffboardPreviewReadMembershipState(StrEnum):
    ENV_LISTED = "env_listed"
    MEMBER = "member"
    NOT_A_MEMBER = "not_a_member"

    def __str__(self) -> str:
        return str(self.value)
