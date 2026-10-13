from enum import StrEnum


class EffectiveShapeSource(StrEnum):
    ASSET = "asset"
    DEFAULT = "default"
    WORKSPACE = "workspace"

    def __str__(self) -> str:
        return str(self.value)
