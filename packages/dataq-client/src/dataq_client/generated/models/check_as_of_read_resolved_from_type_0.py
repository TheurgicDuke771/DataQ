from enum import StrEnum


class CheckAsOfReadResolvedFromType0(StrEnum):
    LATER_VERSION = "later_version"
    LIVE = "live"
    VERSION = "version"

    def __str__(self) -> str:
        return str(self.value)
