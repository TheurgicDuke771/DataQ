from enum import StrEnum


class ChannelCreateType(StrEnum):
    EMAIL = "email"
    SLACK = "slack"
    TEAMS = "teams"
    WEBHOOK = "webhook"

    def __str__(self) -> str:
        return str(self.value)
