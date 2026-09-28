from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.result_read_redaction_type_0 import ResultReadRedactionType0
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.result_read_expected_value_type_0 import ResultReadExpectedValueType0
    from ..models.result_read_observed_value_type_0 import ResultReadObservedValueType0
    from ..models.result_read_sample_failures_type_0 import ResultReadSampleFailuresType0
    from ..models.result_read_sampling_type_0 import ResultReadSamplingType0


T = TypeVar("T", bound="ResultRead")


@_attrs_define
class ResultRead:
    """One check's result within a run. `metric_value` is the SQL-aggregatable
    badness scalar (ADR 0012); `observed_value`/`expected_value` are GX summary values.

    `redaction` (#424, `zero_sample` added #1873): `full`/`partial`/`none` describe
    column-policy-based redaction of a REAL persisted sample; `zero_sample` means this
    deployment's zero-sample privacy mode (`GET /admin/deployment.zero_sample_mode`)
    never persisted a sample for this result at all. Only a `null` sample that genuinely
    never had one — a passing check, or a check kind/type with no row-level data — omits
    the field.
    A "sampled" caveat must key on `sampling.sampled`, NOT rows < total_rows
    (`total_rows` is legitimately null for head samples).

        Attributes:
            check_id (UUID):
            duration_ms (int | None):
            expected_value (None | ResultReadExpectedValueType0):
            id (UUID):
            metric_value (float | None):
            observed_value (None | ResultReadObservedValueType0):
            sample_failures (None | ResultReadSampleFailuresType0):
            status (str):
            redacted_columns (list[str] | Unset):
            redaction (None | ResultReadRedactionType0 | Unset):
            sampling (None | ResultReadSamplingType0 | Unset):
    """

    check_id: UUID
    duration_ms: int | None
    expected_value: None | ResultReadExpectedValueType0
    id: UUID
    metric_value: float | None
    observed_value: None | ResultReadObservedValueType0
    sample_failures: None | ResultReadSampleFailuresType0
    status: str
    redacted_columns: list[str] | Unset = UNSET
    redaction: None | ResultReadRedactionType0 | Unset = UNSET
    sampling: None | ResultReadSamplingType0 | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.result_read_expected_value_type_0 import (
            ResultReadExpectedValueType0,
        )
        from ..models.result_read_observed_value_type_0 import (
            ResultReadObservedValueType0,
        )
        from ..models.result_read_sample_failures_type_0 import (
            ResultReadSampleFailuresType0,
        )
        from ..models.result_read_sampling_type_0 import ResultReadSamplingType0

        check_id = str(self.check_id)

        duration_ms: int | None
        duration_ms = self.duration_ms

        expected_value: dict[str, Any] | None
        if isinstance(self.expected_value, ResultReadExpectedValueType0):
            expected_value = self.expected_value.to_dict()
        else:
            expected_value = self.expected_value

        id = str(self.id)

        metric_value: float | None
        metric_value = self.metric_value

        observed_value: dict[str, Any] | None
        if isinstance(self.observed_value, ResultReadObservedValueType0):
            observed_value = self.observed_value.to_dict()
        else:
            observed_value = self.observed_value

        sample_failures: dict[str, Any] | None
        if isinstance(self.sample_failures, ResultReadSampleFailuresType0):
            sample_failures = self.sample_failures.to_dict()
        else:
            sample_failures = self.sample_failures

        status = self.status

        redacted_columns: list[str] | Unset = UNSET
        if not isinstance(self.redacted_columns, Unset):
            redacted_columns = self.redacted_columns

        redaction: None | str | Unset
        if isinstance(self.redaction, Unset):
            redaction = UNSET
        elif isinstance(self.redaction, ResultReadRedactionType0):
            redaction = self.redaction.value
        else:
            redaction = self.redaction

        sampling: dict[str, Any] | None | Unset
        if isinstance(self.sampling, Unset):
            sampling = UNSET
        elif isinstance(self.sampling, ResultReadSamplingType0):
            sampling = self.sampling.to_dict()
        else:
            sampling = self.sampling

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "check_id": check_id,
                "duration_ms": duration_ms,
                "expected_value": expected_value,
                "id": id,
                "metric_value": metric_value,
                "observed_value": observed_value,
                "sample_failures": sample_failures,
                "status": status,
            }
        )
        if redacted_columns is not UNSET:
            field_dict["redacted_columns"] = redacted_columns
        if redaction is not UNSET:
            field_dict["redaction"] = redaction
        if sampling is not UNSET:
            field_dict["sampling"] = sampling

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.result_read_expected_value_type_0 import (
            ResultReadExpectedValueType0,
        )
        from ..models.result_read_observed_value_type_0 import (
            ResultReadObservedValueType0,
        )
        from ..models.result_read_sample_failures_type_0 import (
            ResultReadSampleFailuresType0,
        )
        from ..models.result_read_sampling_type_0 import ResultReadSamplingType0

        d = dict(src_dict)
        check_id = UUID(d.pop("check_id"))

        def _parse_duration_ms(data: object) -> int | None:
            if data is None:
                return data
            return cast(int | None, data)

        duration_ms = _parse_duration_ms(d.pop("duration_ms"))

        def _parse_expected_value(data: object) -> None | ResultReadExpectedValueType0:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                expected_value_type_0 = ResultReadExpectedValueType0.from_dict(data)

                return expected_value_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | ResultReadExpectedValueType0, data)

        expected_value = _parse_expected_value(d.pop("expected_value"))

        id = UUID(d.pop("id"))

        def _parse_metric_value(data: object) -> float | None:
            if data is None:
                return data
            return cast(float | None, data)

        metric_value = _parse_metric_value(d.pop("metric_value"))

        def _parse_observed_value(data: object) -> None | ResultReadObservedValueType0:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                observed_value_type_0 = ResultReadObservedValueType0.from_dict(data)

                return observed_value_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | ResultReadObservedValueType0, data)

        observed_value = _parse_observed_value(d.pop("observed_value"))

        def _parse_sample_failures(data: object) -> None | ResultReadSampleFailuresType0:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                sample_failures_type_0 = ResultReadSampleFailuresType0.from_dict(data)

                return sample_failures_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | ResultReadSampleFailuresType0, data)

        sample_failures = _parse_sample_failures(d.pop("sample_failures"))

        status = d.pop("status")

        redacted_columns = cast(list[str], d.pop("redacted_columns", UNSET))

        def _parse_redaction(data: object) -> None | ResultReadRedactionType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                redaction_type_0 = ResultReadRedactionType0(data)

                return redaction_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | ResultReadRedactionType0 | Unset, data)

        redaction = _parse_redaction(d.pop("redaction", UNSET))

        def _parse_sampling(data: object) -> None | ResultReadSamplingType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                sampling_type_0 = ResultReadSamplingType0.from_dict(data)

                return sampling_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | ResultReadSamplingType0 | Unset, data)

        sampling = _parse_sampling(d.pop("sampling", UNSET))

        result_read = cls(
            check_id=check_id,
            duration_ms=duration_ms,
            expected_value=expected_value,
            id=id,
            metric_value=metric_value,
            observed_value=observed_value,
            sample_failures=sample_failures,
            status=status,
            redacted_columns=redacted_columns,
            redaction=redaction,
            sampling=sampling,
        )

        result_read.additional_properties = d
        return result_read

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
