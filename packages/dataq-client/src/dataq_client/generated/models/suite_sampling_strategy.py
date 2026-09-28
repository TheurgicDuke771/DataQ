from enum import StrEnum


class SuiteSamplingStrategy(StrEnum):
    HEAD = "head"
    RANDOM = "random"

    def __str__(self) -> str:
        return str(self.value)
