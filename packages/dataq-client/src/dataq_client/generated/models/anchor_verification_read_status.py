from enum import StrEnum


class AnchorVerificationReadStatus(StrEnum):
    EMPTY = "empty"
    MISMATCH = "mismatch"
    OK = "ok"
    UNAVAILABLE = "unavailable"

    def __str__(self) -> str:
        return str(self.value)
