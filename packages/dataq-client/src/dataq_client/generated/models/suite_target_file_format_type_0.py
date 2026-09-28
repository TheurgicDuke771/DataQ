from enum import StrEnum


class SuiteTargetFileFormatType0(StrEnum):
    CSV = "csv"
    PARQUET = "parquet"

    def __str__(self) -> str:
        return str(self.value)
