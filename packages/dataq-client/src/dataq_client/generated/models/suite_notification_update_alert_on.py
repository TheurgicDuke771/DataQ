from enum import StrEnum


class SuiteNotificationUpdateAlertOn(StrEnum):
    ALWAYS = "always"
    FAIL = "fail"
    WARN = "warn"

    def __str__(self) -> str:
        return str(self.value)
