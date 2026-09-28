from enum import StrEnum


class SecretSweepReportReadModeType0(StrEnum):
    PURGE = "purge"
    REPORT = "report"

    def __str__(self) -> str:
        return str(self.value)
