from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="OnboardingStatusRead")


@_attrs_define
class OnboardingStatusRead:
    """Which first-run steps the workspace has done.

    Workspace-wide: true if ANY connection, suite, check or run exists, including ones the
    caller cannot open, so every member sees the same answer. Booleans only. An
    orchestration connection (ADF, Airflow, dbt) does not count as a data source.

        Attributes:
            complete (bool):
            has_check (bool):
            has_datasource (bool):
            has_run (bool):
            has_suite (bool):
    """

    complete: bool
    has_check: bool
    has_datasource: bool
    has_run: bool
    has_suite: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        complete = self.complete

        has_check = self.has_check

        has_datasource = self.has_datasource

        has_run = self.has_run

        has_suite = self.has_suite

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "complete": complete,
                "has_check": has_check,
                "has_datasource": has_datasource,
                "has_run": has_run,
                "has_suite": has_suite,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        complete = d.pop("complete")

        has_check = d.pop("has_check")

        has_datasource = d.pop("has_datasource")

        has_run = d.pop("has_run")

        has_suite = d.pop("has_suite")

        onboarding_status_read = cls(
            complete=complete,
            has_check=has_check,
            has_datasource=has_datasource,
            has_run=has_run,
            has_suite=has_suite,
        )

        onboarding_status_read.additional_properties = d
        return onboarding_status_read

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
