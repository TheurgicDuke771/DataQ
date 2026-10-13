from enum import StrEnum


class InheritedFromSensitivity(StrEnum):
    ASSET = "asset"
    DEFAULT = "default"
    WORKSPACE = "workspace"

    def __str__(self) -> str:
        return str(self.value)
