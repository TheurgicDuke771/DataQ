from enum import StrEnum


class GateRequestFailOn(StrEnum):
    CRITICAL = "critical"
    FAIL = "fail"
    WARN = "warn"

    def __str__(self) -> str:
        return str(self.value)
