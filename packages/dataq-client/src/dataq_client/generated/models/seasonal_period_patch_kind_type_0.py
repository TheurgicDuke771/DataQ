from enum import StrEnum


class SeasonalPeriodPatchKindType0(StrEnum):
    DATED = "dated"
    RECURRING = "recurring"

    def __str__(self) -> str:
        return str(self.value)
