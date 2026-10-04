from enum import StrEnum


class ListAssetsSort(StrEnum):
    HEALTH_SCORE = "health_score"
    NAME = "name"

    def __str__(self) -> str:
        return str(self.value)
