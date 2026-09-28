from enum import StrEnum


class PollHealthReadStatus(StrEnum):
    FAILING = "failing"
    ON_CADENCE = "on_cadence"
    STALLED = "stalled"
    UNKNOWN = "unknown"

    def __str__(self) -> str:
        return str(self.value)
