from enum import StrEnum


class GateRequestEnv(StrEnum):
    DEV = "dev"
    PROD = "prod"
    QA = "qa"
    UAT = "uat"

    def __str__(self) -> str:
        return str(self.value)
