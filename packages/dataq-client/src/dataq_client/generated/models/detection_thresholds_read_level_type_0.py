from enum import StrEnum


class DetectionThresholdsReadLevelType0(StrEnum):
    MEAN = "mean"
    MEDIAN = "median"

    def __str__(self) -> str:
        return str(self.value)
