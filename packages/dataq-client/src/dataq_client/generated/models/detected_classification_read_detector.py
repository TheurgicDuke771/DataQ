from enum import StrEnum


class DetectedClassificationReadDetector(StrEnum):
    EMAIL = "email"
    ENCODED = "encoded"

    def __str__(self) -> str:
        return str(self.value)
