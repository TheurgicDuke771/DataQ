from enum import StrEnum


class PreviewBatchTargetStrategy(StrEnum):
    LATEST = "latest"
    SPECIFIC = "specific"

    def __str__(self) -> str:
        return str(self.value)
