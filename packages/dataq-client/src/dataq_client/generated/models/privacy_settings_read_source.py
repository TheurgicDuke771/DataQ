from enum import StrEnum


class PrivacySettingsReadSource(StrEnum):
    DB = "db"
    ENV = "env"
    OFF = "off"

    def __str__(self) -> str:
        return str(self.value)
