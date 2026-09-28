from enum import StrEnum


class ColumnProfileRequestFileFormatType0(StrEnum):
    CSV = "csv"
    PARQUET = "parquet"

    def __str__(self) -> str:
        return str(self.value)
