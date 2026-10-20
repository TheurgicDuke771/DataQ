from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from typing_extensions import Self

T = TypeVar("T", bound="ClassificationDismissUpdate")


@_attrs_define
class ClassificationDismissUpdate:
    """Dismiss (`true`) or restore (`false`) one profiler detection — the only way a detection
    is cleared, so it is workspace-Admin-only and audited (ADR 0049 §4). The column travels in
    the body: a column name may hold `/` (quoted identifiers, file headers), which a path
    segment cannot carry.

        Attributes:
            column (str):
            dismissed (bool):
    """

    column: str
    dismissed: bool

    def to_dict(self) -> dict[str, Any]:
        column = self.column

        dismissed = self.dismissed

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "column": column,
                "dismissed": dismissed,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        column = d.pop("column")

        dismissed = d.pop("dismissed")

        classification_dismiss_update = cls(
            column=column,
            dismissed=dismissed,
        )

        return classification_dismiss_update
