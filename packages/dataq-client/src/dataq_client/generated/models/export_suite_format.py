from enum import StrEnum


class ExportSuiteFormat(StrEnum):
    JSON = "json"
    YAML = "yaml"

    def __str__(self) -> str:
        return str(self.value)
