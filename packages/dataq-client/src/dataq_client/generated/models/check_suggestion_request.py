from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar
from uuid import UUID

from attrs import define as _attrs_define
from typing_extensions import Self

T = TypeVar("T", bound="CheckSuggestionRequest")


@_attrs_define
class CheckSuggestionRequest:
    """
    Attributes:
        suite_id (UUID):
    """

    suite_id: UUID

    def to_dict(self) -> dict[str, Any]:
        suite_id = str(self.suite_id)

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "suite_id": suite_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        suite_id = UUID(d.pop("suite_id"))

        check_suggestion_request = cls(
            suite_id=suite_id,
        )

        return check_suggestion_request
