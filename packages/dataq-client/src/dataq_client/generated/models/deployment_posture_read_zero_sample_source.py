from enum import StrEnum


class DeploymentPostureReadZeroSampleSource(StrEnum):
    DB = "db"
    ENV = "env"
    OFF = "off"

    def __str__(self) -> str:
        return str(self.value)
