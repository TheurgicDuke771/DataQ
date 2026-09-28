from enum import StrEnum


class TraceDirection(StrEnum):
    BOTH = "both"
    DOWNSTREAM = "downstream"
    UPSTREAM = "upstream"

    def __str__(self) -> str:
        return str(self.value)
