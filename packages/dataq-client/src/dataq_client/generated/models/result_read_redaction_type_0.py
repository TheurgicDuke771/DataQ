from enum import StrEnum


class ResultReadRedactionType0(StrEnum):
    FULL = "full"
    NONE = "none"
    PARTIAL = "partial"
    ZERO_SAMPLE = "zero_sample"

    def __str__(self) -> str:
        return str(self.value)
