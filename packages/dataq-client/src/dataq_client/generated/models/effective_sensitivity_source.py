from enum import StrEnum


class EffectiveSensitivitySource(StrEnum):
    ASSET = "asset"
    DEFAULT = "default"
    WORKSPACE = "workspace"

    def __str__(self) -> str:
        return str(self.value)
