from enum import StrEnum


class AnchorFindingMatch(StrEnum):
    MALFORMED = "malformed"
    MISSING = "missing"
    PRESENT = "present"
    PURGED = "purged"

    def __str__(self) -> str:
        return str(self.value)
