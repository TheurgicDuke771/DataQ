from enum import StrEnum


class SecretSweepReportReadStatus(StrEnum):
    NEVER_RUN = "never_run"
    RECORDED = "recorded"
    SKIPPED = "skipped"

    def __str__(self) -> str:
        return str(self.value)
