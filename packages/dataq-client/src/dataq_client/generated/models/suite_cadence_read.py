from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="SuiteCadenceRead")


@_attrs_define
class SuiteCadenceRead:
    """A suite's bound-pipeline cadence (#1648) — the deterministic freshness-
    threshold hint the LLM suggestion prompt also uses when one is configured,
    but computed here without any LLM at all.

        Attributes:
            bound (bool):
            env (None | str | Unset):
            insufficient_history (bool | Unset):  Default: True.
            max_gap_hours (float | None | Unset):
            median_gap_hours (float | None | Unset):
            pipeline_or_dag_id (None | str | Unset):
            provider (None | str | Unset):
            sample_count (int | Unset):  Default: 0.
            suggested_fail_threshold_hours (float | None | Unset):
    """

    bound: bool
    env: None | str | Unset = UNSET
    insufficient_history: bool | Unset = True
    max_gap_hours: float | None | Unset = UNSET
    median_gap_hours: float | None | Unset = UNSET
    pipeline_or_dag_id: None | str | Unset = UNSET
    provider: None | str | Unset = UNSET
    sample_count: int | Unset = 0
    suggested_fail_threshold_hours: float | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        bound = self.bound

        env: None | str | Unset
        if isinstance(self.env, Unset):
            env = UNSET
        else:
            env = self.env

        insufficient_history = self.insufficient_history

        max_gap_hours: float | None | Unset
        if isinstance(self.max_gap_hours, Unset):
            max_gap_hours = UNSET
        else:
            max_gap_hours = self.max_gap_hours

        median_gap_hours: float | None | Unset
        if isinstance(self.median_gap_hours, Unset):
            median_gap_hours = UNSET
        else:
            median_gap_hours = self.median_gap_hours

        pipeline_or_dag_id: None | str | Unset
        if isinstance(self.pipeline_or_dag_id, Unset):
            pipeline_or_dag_id = UNSET
        else:
            pipeline_or_dag_id = self.pipeline_or_dag_id

        provider: None | str | Unset
        if isinstance(self.provider, Unset):
            provider = UNSET
        else:
            provider = self.provider

        sample_count = self.sample_count

        suggested_fail_threshold_hours: float | None | Unset
        if isinstance(self.suggested_fail_threshold_hours, Unset):
            suggested_fail_threshold_hours = UNSET
        else:
            suggested_fail_threshold_hours = self.suggested_fail_threshold_hours

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "bound": bound,
            }
        )
        if env is not UNSET:
            field_dict["env"] = env
        if insufficient_history is not UNSET:
            field_dict["insufficient_history"] = insufficient_history
        if max_gap_hours is not UNSET:
            field_dict["max_gap_hours"] = max_gap_hours
        if median_gap_hours is not UNSET:
            field_dict["median_gap_hours"] = median_gap_hours
        if pipeline_or_dag_id is not UNSET:
            field_dict["pipeline_or_dag_id"] = pipeline_or_dag_id
        if provider is not UNSET:
            field_dict["provider"] = provider
        if sample_count is not UNSET:
            field_dict["sample_count"] = sample_count
        if suggested_fail_threshold_hours is not UNSET:
            field_dict["suggested_fail_threshold_hours"] = suggested_fail_threshold_hours

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        bound = d.pop("bound")

        def _parse_env(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        env = _parse_env(d.pop("env", UNSET))

        insufficient_history = d.pop("insufficient_history", UNSET)

        def _parse_max_gap_hours(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        max_gap_hours = _parse_max_gap_hours(d.pop("max_gap_hours", UNSET))

        def _parse_median_gap_hours(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        median_gap_hours = _parse_median_gap_hours(d.pop("median_gap_hours", UNSET))

        def _parse_pipeline_or_dag_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        pipeline_or_dag_id = _parse_pipeline_or_dag_id(d.pop("pipeline_or_dag_id", UNSET))

        def _parse_provider(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        provider = _parse_provider(d.pop("provider", UNSET))

        sample_count = d.pop("sample_count", UNSET)

        def _parse_suggested_fail_threshold_hours(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        suggested_fail_threshold_hours = _parse_suggested_fail_threshold_hours(
            d.pop("suggested_fail_threshold_hours", UNSET)
        )

        suite_cadence_read = cls(
            bound=bound,
            env=env,
            insufficient_history=insufficient_history,
            max_gap_hours=max_gap_hours,
            median_gap_hours=median_gap_hours,
            pipeline_or_dag_id=pipeline_or_dag_id,
            provider=provider,
            sample_count=sample_count,
            suggested_fail_threshold_hours=suggested_fail_threshold_hours,
        )

        suite_cadence_read.additional_properties = d
        return suite_cadence_read

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
