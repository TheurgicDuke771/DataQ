from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.comparison_check_read import ComparisonCheckRead
    from ..models.comparison_side_read_observed_value_type_0 import (
        ComparisonSideReadObservedValueType0,
    )


T = TypeVar("T", bound="ComparisonSideRead")


@_attrs_define
class ComparisonSideRead:
    """One run's result for a check. `observed_value` is redacted exactly as on
    `GET /runs/{id}`; failing-row samples are never carried here.

        Attributes:
            check (ComparisonCheckRead): A check's identity as it WAS when one side's result was written (from
                `check_versions`), never its current state — the two sides of a row may differ.

                `resolved_from` (#1613) says which: `version` = a `check_versions` row in effect at
                the result's `created_at`; `live` = the check has NO version row, so this is its
                current state and may postdate the result — a reader must say so.
            metric_value (float | None):
            observed_value (ComparisonSideReadObservedValueType0 | None):
            result_id (UUID):
            status (str):
    """

    check: ComparisonCheckRead
    metric_value: float | None
    observed_value: ComparisonSideReadObservedValueType0 | None
    result_id: UUID
    status: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.comparison_side_read_observed_value_type_0 import (
            ComparisonSideReadObservedValueType0,
        )

        check = self.check.to_dict()

        metric_value: float | None
        metric_value = self.metric_value

        observed_value: dict[str, Any] | None
        if isinstance(self.observed_value, ComparisonSideReadObservedValueType0):
            observed_value = self.observed_value.to_dict()
        else:
            observed_value = self.observed_value

        result_id = str(self.result_id)

        status = self.status

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "check": check,
                "metric_value": metric_value,
                "observed_value": observed_value,
                "result_id": result_id,
                "status": status,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.comparison_check_read import ComparisonCheckRead
        from ..models.comparison_side_read_observed_value_type_0 import (
            ComparisonSideReadObservedValueType0,
        )

        d = dict(src_dict)
        check = ComparisonCheckRead.from_dict(d.pop("check"))

        def _parse_metric_value(data: object) -> float | None:
            if data is None:
                return data
            return cast(float | None, data)

        metric_value = _parse_metric_value(d.pop("metric_value"))

        def _parse_observed_value(data: object) -> ComparisonSideReadObservedValueType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                observed_value_type_0 = ComparisonSideReadObservedValueType0.from_dict(data)

                return observed_value_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ComparisonSideReadObservedValueType0 | None, data)

        observed_value = _parse_observed_value(d.pop("observed_value"))

        result_id = UUID(d.pop("result_id"))

        status = d.pop("status")

        comparison_side_read = cls(
            check=check,
            metric_value=metric_value,
            observed_value=observed_value,
            result_id=result_id,
            status=status,
        )

        comparison_side_read.additional_properties = d
        return comparison_side_read

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
