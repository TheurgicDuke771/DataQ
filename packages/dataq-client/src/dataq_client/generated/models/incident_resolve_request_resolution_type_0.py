from enum import StrEnum


class IncidentResolveRequestResolutionType0(StrEnum):
    EXPECTED_CHANGE = "expected_change"
    FALSE_POSITIVE = "false_positive"
    FIXED = "fixed"

    def __str__(self) -> str:
        return str(self.value)
