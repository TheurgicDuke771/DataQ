from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.comparison_row_read import ComparisonRowRead
    from ..models.comparison_run_read import ComparisonRunRead
    from ..models.comparison_summary_read import ComparisonSummaryRead


T = TypeVar("T", bound="RunComparisonRead")


@_attrs_define
class RunComparisonRead:
    """Two runs of one suite diffed check by check: `base` is the run in the path,
    `target` the other. Rows are ordered by label.

        Attributes:
            base (ComparisonRunRead):
            rows (list[ComparisonRowRead]):
            summary (ComparisonSummaryRead):
            target (ComparisonRunRead):
    """

    base: ComparisonRunRead
    rows: list[ComparisonRowRead]
    summary: ComparisonSummaryRead
    target: ComparisonRunRead
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        base = self.base.to_dict()

        rows = []
        for rows_item_data in self.rows:
            rows_item = rows_item_data.to_dict()
            rows.append(rows_item)

        summary = self.summary.to_dict()

        target = self.target.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "base": base,
                "rows": rows,
                "summary": summary,
                "target": target,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.comparison_row_read import ComparisonRowRead
        from ..models.comparison_run_read import ComparisonRunRead
        from ..models.comparison_summary_read import ComparisonSummaryRead

        d = dict(src_dict)
        base = ComparisonRunRead.from_dict(d.pop("base"))

        rows = []
        _rows = d.pop("rows")
        for rows_item_data in _rows:
            rows_item = ComparisonRowRead.from_dict(rows_item_data)

            rows.append(rows_item)

        summary = ComparisonSummaryRead.from_dict(d.pop("summary"))

        target = ComparisonRunRead.from_dict(d.pop("target"))

        run_comparison_read = cls(
            base=base,
            rows=rows,
            summary=summary,
            target=target,
        )

        run_comparison_read.additional_properties = d
        return run_comparison_read

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
