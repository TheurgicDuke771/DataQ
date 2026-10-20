from enum import StrEnum


class DetectedClassificationReadEffect(StrEnum):
    DISMISSED = "dismissed"
    MASKS = "masks"
    OVERRIDDEN_BY_TAG = "overridden_by_tag"

    def __str__(self) -> str:
        return str(self.value)
