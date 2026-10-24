from enum import StrEnum


class SearchResponseTruncatedGroupsItem(StrEnum):
    CHECKS = "checks"
    CONNECTIONS = "connections"
    RUNS = "runs"
    SUITES = "suites"

    def __str__(self) -> str:
        return str(self.value)
