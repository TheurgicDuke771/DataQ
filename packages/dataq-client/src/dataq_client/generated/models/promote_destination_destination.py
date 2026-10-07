from enum import StrEnum


class PromoteDestinationDestination(StrEnum):
    EMAIL = "email"
    SLACK = "slack"
    TEAMS = "teams"

    def __str__(self) -> str:
        return str(self.value)
