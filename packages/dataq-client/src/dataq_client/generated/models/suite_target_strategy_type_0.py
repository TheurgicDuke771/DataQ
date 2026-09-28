from enum import StrEnum


class SuiteTargetStrategyType0(StrEnum):
    LATEST = "latest"
    SPECIFIC = "specific"

    def __str__(self) -> str:
        return str(self.value)
