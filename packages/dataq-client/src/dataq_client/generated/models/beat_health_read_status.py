from enum import StrEnum


class BeatHealthReadStatus(StrEnum):
    ALIVE = "alive"
    NOT_MONITORED = "not_monitored"
    STALE = "stale"

    def __str__(self) -> str:
        return str(self.value)
