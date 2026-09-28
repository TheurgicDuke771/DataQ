from enum import StrEnum


class CatalogBrowseReadLevel(StrEnum):
    CATALOG = "catalog"
    SCHEMA = "schema"
    TABLE = "table"

    def __str__(self) -> str:
        return str(self.value)
