from enum import StrEnum


class GateReadState(StrEnum):
    AWAITING_TRIGGER = "awaiting_trigger"
    ERROR = "error"
    FAILED = "failed"
    PASSED = "passed"
    RUNNING = "running"

    def __str__(self) -> str:
        return str(self.value)
