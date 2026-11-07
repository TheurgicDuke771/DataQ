from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.comparison_side_read_metric_value_redaction_type_0 import (
    ComparisonSideReadMetricValueRedactionType0,
)
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.check_as_of_read import CheckAsOfRead
    from ..models.comparison_side_read_observed_value_type_0 import (
        ComparisonSideReadObservedValueType0,
    )


T = TypeVar("T", bound="ComparisonSideRead")


@_attrs_define
class ComparisonSideRead:
    """One run's result for a check. `observed_value` is redacted exactly as on
    `GET /runs/{id}`; failing-row samples are never carried here.

        Attributes:
            check (CheckAsOfRead): A check's identity as it WAS when a result was written — `engine` (ADR 0036),
                `dimension` (ADR 0038), `expectation_type`, `kind`, name and tested column resolved
                from `check_versions` at the result's own `created_at`, never the check's current
                row — so a check re-pointed or re-classified after a run never relabels that run's
                results (#1489/#1613). The two sides of a comparison row may therefore differ.

                `resolved_from` says how trustworthy that is: `version` = a snapshot in effect at the
                result; `later_version` = every snapshot postdates the result (clock skew), so the
                earliest stands in; `live` = the check has no version history, so its CURRENT row
                stands in and may postdate the result. Only `version` is genuinely as-of.
            metric_value (float | None):
            observed_value (ComparisonSideReadObservedValueType0 | None):
            result_id (UUID):
            status (str):
            metric_value_redaction (ComparisonSideReadMetricValueRedactionType0 | None | Unset):
    """

    check: CheckAsOfRead
    metric_value: float | None
    observed_value: ComparisonSideReadObservedValueType0 | None
    result_id: UUID
    status: str
    metric_value_redaction: ComparisonSideReadMetricValueRedactionType0 | None | Unset = UNSET
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

        metric_value_redaction: None | str | Unset
        if isinstance(self.metric_value_redaction, Unset):
            metric_value_redaction = UNSET
        elif isinstance(self.metric_value_redaction, ComparisonSideReadMetricValueRedactionType0):
            metric_value_redaction = self.metric_value_redaction.value
        else:
            metric_value_redaction = self.metric_value_redaction

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
        if metric_value_redaction is not UNSET:
            field_dict["metric_value_redaction"] = metric_value_redaction

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.check_as_of_read import CheckAsOfRead
        from ..models.comparison_side_read_observed_value_type_0 import (
            ComparisonSideReadObservedValueType0,
        )

        d = dict(src_dict)
        check = CheckAsOfRead.from_dict(d.pop("check"))

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

        def _parse_metric_value_redaction(
            data: object,
        ) -> ComparisonSideReadMetricValueRedactionType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                metric_value_redaction_type_0 = ComparisonSideReadMetricValueRedactionType0(data)

                return metric_value_redaction_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ComparisonSideReadMetricValueRedactionType0 | None | Unset, data)

        metric_value_redaction = _parse_metric_value_redaction(
            d.pop("metric_value_redaction", UNSET)
        )

        comparison_side_read = cls(
            check=check,
            metric_value=metric_value,
            observed_value=observed_value,
            result_id=result_id,
            status=status,
            metric_value_redaction=metric_value_redaction,
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
