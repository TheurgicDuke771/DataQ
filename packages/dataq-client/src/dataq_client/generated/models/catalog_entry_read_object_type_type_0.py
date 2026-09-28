from enum import StrEnum


class CatalogEntryReadObjectTypeType0(StrEnum):
    DYNAMIC_TABLE = "dynamic_table"
    MATERIALIZED_VIEW = "materialized_view"
    STREAMING_TABLE = "streaming_table"
    TABLE = "table"
    VIEW = "view"

    def __str__(self) -> str:
        return str(self.value)
