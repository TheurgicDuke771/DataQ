from enum import StrEnum


class ListSuggestionsStatus(StrEnum):
    ACCEPTED = "accepted"
    ALL = "all"
    PENDING = "pending"
    REJECTED = "rejected"

    def __str__(self) -> str:
        return str(self.value)
