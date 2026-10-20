from enum import StrEnum


class SeasonalPeriodReadKind(StrEnum):
    DATED = "dated"
    RECURRING = "recurring"

    def __str__(self) -> str:
        return str(self.value)
