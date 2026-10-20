from enum import StrEnum


class DetectedPeriodReadKind(StrEnum):
    RECURRING = "recurring"
    SEEN_ONCE = "seen_once"

    def __str__(self) -> str:
        return str(self.value)
