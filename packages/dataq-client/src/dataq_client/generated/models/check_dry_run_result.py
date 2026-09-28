from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.check_dry_run_result_expected_value_type_0 import (
        CheckDryRunResultExpectedValueType0,
    )
    from ..models.check_dry_run_result_observed_value_type_0 import (
        CheckDryRunResultObservedValueType0,
    )


T = TypeVar("T", bound="CheckDryRunResult")


@_attrs_define
class CheckDryRunResult:
    """
    Attributes:
        expected_value (CheckDryRunResultExpectedValueType0 | None):
        metric_value (float | None):
        observed_value (CheckDryRunResultObservedValueType0 | None):
        status (str):
    """

    expected_value: CheckDryRunResultExpectedValueType0 | None
    metric_value: float | None
    observed_value: CheckDryRunResultObservedValueType0 | None
    status: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.check_dry_run_result_expected_value_type_0 import (
            CheckDryRunResultExpectedValueType0,
        )
        from ..models.check_dry_run_result_observed_value_type_0 import (
            CheckDryRunResultObservedValueType0,
        )

        expected_value: dict[str, Any] | None
        if isinstance(self.expected_value, CheckDryRunResultExpectedValueType0):
            expected_value = self.expected_value.to_dict()
        else:
            expected_value = self.expected_value

        metric_value: float | None
        metric_value = self.metric_value

        observed_value: dict[str, Any] | None
        if isinstance(self.observed_value, CheckDryRunResultObservedValueType0):
            observed_value = self.observed_value.to_dict()
        else:
            observed_value = self.observed_value

        status = self.status

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "expected_value": expected_value,
                "metric_value": metric_value,
                "observed_value": observed_value,
                "status": status,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.check_dry_run_result_expected_value_type_0 import (
            CheckDryRunResultExpectedValueType0,
        )
        from ..models.check_dry_run_result_observed_value_type_0 import (
            CheckDryRunResultObservedValueType0,
        )

        d = dict(src_dict)

        def _parse_expected_value(data: object) -> CheckDryRunResultExpectedValueType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                expected_value_type_0 = CheckDryRunResultExpectedValueType0.from_dict(data)

                return expected_value_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(CheckDryRunResultExpectedValueType0 | None, data)

        expected_value = _parse_expected_value(d.pop("expected_value"))

        def _parse_metric_value(data: object) -> float | None:
            if data is None:
                return data
            return cast(float | None, data)

        metric_value = _parse_metric_value(d.pop("metric_value"))

        def _parse_observed_value(data: object) -> CheckDryRunResultObservedValueType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                observed_value_type_0 = CheckDryRunResultObservedValueType0.from_dict(data)

                return observed_value_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(CheckDryRunResultObservedValueType0 | None, data)

        observed_value = _parse_observed_value(d.pop("observed_value"))

        status = d.pop("status")

        check_dry_run_result = cls(
            expected_value=expected_value,
            metric_value=metric_value,
            observed_value=observed_value,
            status=status,
        )

        check_dry_run_result.additional_properties = d
        return check_dry_run_result

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
