from enum import StrEnum


class SuiteTargetFileFormatType0(StrEnum):
    CSV = "csv"
    JSON = "json"
    PARQUET = "parquet"

    def __str__(self) -> str:
        return str(self.value)
