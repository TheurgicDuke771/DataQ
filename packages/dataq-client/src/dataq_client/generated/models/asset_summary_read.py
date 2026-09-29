from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="AssetSummaryRead")


@_attrs_define
class AssetSummaryRead:
    """List-row aggregation for one asset — **workspace-true** (ADR 0037): every
    field is identical for every viewer, aggregated over ALL composing suites.
    Carries **two orthogonal health axes** (#803) the UI renders separately:

        Attributes:
            checks_passed (int):
            checks_total (int):
            description (None | str):
            env (None | str):
            has_active_run (bool):
            has_cancelled_run (bool):
            has_failed_run (bool):
            has_operational_error (bool):
            has_skip (bool):
            id (UUID):
            last_run_at (datetime.datetime | None):
            last_seen (datetime.datetime):
            name (str):
            namespace (str):
            owner_user_id (None | UUID):
            suite_count (int):
            worst_severity (None | str):
            auto_coverage_excluded (bool | Unset):  Default: False.
    """

    checks_passed: int
    checks_total: int
    description: None | str
    env: None | str
    has_active_run: bool
    has_cancelled_run: bool
    has_failed_run: bool
    has_operational_error: bool
    has_skip: bool
    id: UUID
    last_run_at: datetime.datetime | None
    last_seen: datetime.datetime
    name: str
    namespace: str
    owner_user_id: None | UUID
    suite_count: int
    worst_severity: None | str
    auto_coverage_excluded: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        checks_passed = self.checks_passed

        checks_total = self.checks_total

        description: None | str
        description = self.description

        env: None | str
        env = self.env

        has_active_run = self.has_active_run

        has_cancelled_run = self.has_cancelled_run

        has_failed_run = self.has_failed_run

        has_operational_error = self.has_operational_error

        has_skip = self.has_skip

        id = str(self.id)

        last_run_at: None | str
        if isinstance(self.last_run_at, datetime.datetime):
            last_run_at = self.last_run_at.isoformat()
        else:
            last_run_at = self.last_run_at

        last_seen = self.last_seen.isoformat()

        name = self.name

        namespace = self.namespace

        owner_user_id: None | str
        if isinstance(self.owner_user_id, UUID):
            owner_user_id = str(self.owner_user_id)
        else:
            owner_user_id = self.owner_user_id

        suite_count = self.suite_count

        worst_severity: None | str
        worst_severity = self.worst_severity

        auto_coverage_excluded = self.auto_coverage_excluded

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "checks_passed": checks_passed,
                "checks_total": checks_total,
                "description": description,
                "env": env,
                "has_active_run": has_active_run,
                "has_cancelled_run": has_cancelled_run,
                "has_failed_run": has_failed_run,
                "has_operational_error": has_operational_error,
                "has_skip": has_skip,
                "id": id,
                "last_run_at": last_run_at,
                "last_seen": last_seen,
                "name": name,
                "namespace": namespace,
                "owner_user_id": owner_user_id,
                "suite_count": suite_count,
                "worst_severity": worst_severity,
            }
        )
        if auto_coverage_excluded is not UNSET:
            field_dict["auto_coverage_excluded"] = auto_coverage_excluded

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        checks_passed = d.pop("checks_passed")

        checks_total = d.pop("checks_total")

        def _parse_description(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        description = _parse_description(d.pop("description"))

        def _parse_env(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        env = _parse_env(d.pop("env"))

        has_active_run = d.pop("has_active_run")

        has_cancelled_run = d.pop("has_cancelled_run")

        has_failed_run = d.pop("has_failed_run")

        has_operational_error = d.pop("has_operational_error")

        has_skip = d.pop("has_skip")

        id = UUID(d.pop("id"))

        def _parse_last_run_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_run_at_type_0 = datetime.datetime.fromisoformat(data)

                return last_run_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        last_run_at = _parse_last_run_at(d.pop("last_run_at"))

        last_seen = datetime.datetime.fromisoformat(d.pop("last_seen"))

        name = d.pop("name")

        namespace = d.pop("namespace")

        def _parse_owner_user_id(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                owner_user_id_type_0 = UUID(data)

                return owner_user_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | UUID, data)

        owner_user_id = _parse_owner_user_id(d.pop("owner_user_id"))

        suite_count = d.pop("suite_count")

        def _parse_worst_severity(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        worst_severity = _parse_worst_severity(d.pop("worst_severity"))

        auto_coverage_excluded = d.pop("auto_coverage_excluded", UNSET)

        asset_summary_read = cls(
            checks_passed=checks_passed,
            checks_total=checks_total,
            description=description,
            env=env,
            has_active_run=has_active_run,
            has_cancelled_run=has_cancelled_run,
            has_failed_run=has_failed_run,
            has_operational_error=has_operational_error,
            has_skip=has_skip,
            id=id,
            last_run_at=last_run_at,
            last_seen=last_seen,
            name=name,
            namespace=namespace,
            owner_user_id=owner_user_id,
            suite_count=suite_count,
            worst_severity=worst_severity,
            auto_coverage_excluded=auto_coverage_excluded,
        )

        asset_summary_read.additional_properties = d
        return asset_summary_read

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
