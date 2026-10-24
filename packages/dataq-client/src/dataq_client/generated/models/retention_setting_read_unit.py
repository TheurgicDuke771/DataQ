from enum import StrEnum


class RetentionSettingReadUnit(StrEnum):
    DAYS = "days"
    HOURS = "hours"

    def __str__(self) -> str:
        return str(self.value)
