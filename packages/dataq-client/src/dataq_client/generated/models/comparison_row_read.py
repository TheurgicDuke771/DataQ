from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.comparison_delta_read import ComparisonDeltaRead
    from ..models.comparison_side_read import ComparisonSideRead


T = TypeVar("T", bound="ComparisonRowRead")


@_attrs_define
class ComparisonRowRead:
    """
    Attributes:
        base (ComparisonSideRead | None):
        check_id (UUID):
        delta (ComparisonDeltaRead): `status_change`: `improved`/`regressed` rank pass < warn < fail < critical;
            `changed` is a transition through `skip`/`error`, which carry no rank;
            `added`/`removed` mean the check has a result on only one side. `metric_delta`
            is target minus base, `metric_delta_pct` relative to a non-zero base; both null
            when either side has no metric. `metric_changed` is true whenever the two
            metrics differ — including when only ONE side has one, in which case the deltas
            are null because no difference can be stated.
        label (str):
        target (ComparisonSideRead | None):
    """

    base: ComparisonSideRead | None
    check_id: UUID
    delta: ComparisonDeltaRead
    label: str
    target: ComparisonSideRead | None
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.comparison_side_read import ComparisonSideRead

        base: dict[str, Any] | None
        if isinstance(self.base, ComparisonSideRead):
            base = self.base.to_dict()
        else:
            base = self.base

        check_id = str(self.check_id)

        delta = self.delta.to_dict()

        label = self.label

        target: dict[str, Any] | None
        if isinstance(self.target, ComparisonSideRead):
            target = self.target.to_dict()
        else:
            target = self.target

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "base": base,
                "check_id": check_id,
                "delta": delta,
                "label": label,
                "target": target,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.comparison_delta_read import ComparisonDeltaRead
        from ..models.comparison_side_read import ComparisonSideRead

        d = dict(src_dict)

        def _parse_base(data: object) -> ComparisonSideRead | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                base_type_0 = ComparisonSideRead.from_dict(data)

                return base_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ComparisonSideRead | None, data)

        base = _parse_base(d.pop("base"))

        check_id = UUID(d.pop("check_id"))

        delta = ComparisonDeltaRead.from_dict(d.pop("delta"))

        label = d.pop("label")

        def _parse_target(data: object) -> ComparisonSideRead | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                target_type_0 = ComparisonSideRead.from_dict(data)

                return target_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ComparisonSideRead | None, data)

        target = _parse_target(d.pop("target"))

        comparison_row_read = cls(
            base=base,
            check_id=check_id,
            delta=delta,
            label=label,
            target=target,
        )

        comparison_row_read.additional_properties = d
        return comparison_row_read

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
