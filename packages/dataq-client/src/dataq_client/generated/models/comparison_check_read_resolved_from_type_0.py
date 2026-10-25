from enum import StrEnum


class ComparisonCheckReadResolvedFromType0(StrEnum):
    LIVE = "live"
    VERSION = "version"

    def __str__(self) -> str:
        return str(self.value)
