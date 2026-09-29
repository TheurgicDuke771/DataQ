from enum import StrEnum


class ColumnPolicySuggestRequestFileFormatType0(StrEnum):
    CSV = "csv"
    JSON = "json"
    PARQUET = "parquet"

    def __str__(self) -> str:
        return str(self.value)
