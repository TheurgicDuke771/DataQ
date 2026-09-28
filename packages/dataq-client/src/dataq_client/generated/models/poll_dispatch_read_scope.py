from enum import StrEnum


class PollDispatchReadScope(StrEnum):
    CONNECTION = "connection"
    PROVIDER = "provider"

    def __str__(self) -> str:
        return str(self.value)
