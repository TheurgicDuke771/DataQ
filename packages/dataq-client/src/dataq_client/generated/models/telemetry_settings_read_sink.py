from enum import StrEnum


class TelemetrySettingsReadSink(StrEnum):
    FILE = "file"
    OFF = "off"
    OTLP = "otlp"

    def __str__(self) -> str:
        return str(self.value)
