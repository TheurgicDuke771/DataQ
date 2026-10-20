from enum import StrEnum


class SeasonalPeriodWriteKind(StrEnum):
    DATED = "dated"
    RECURRING = "recurring"

    def __str__(self) -> str:
        return str(self.value)
