from enum import StrEnum


class ComparisonDeltaReadStatusChange(StrEnum):
    ADDED = "added"
    CHANGED = "changed"
    IMPROVED = "improved"
    REGRESSED = "regressed"
    REMOVED = "removed"
    SAME = "same"

    def __str__(self) -> str:
        return str(self.value)
