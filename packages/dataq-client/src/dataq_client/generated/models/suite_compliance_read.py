from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="SuiteComplianceRead")


@_attrs_define
class SuiteComplianceRead:
    """
    Attributes:
        last_run_at (datetime.datetime | None):
        last_run_id (None | UUID):
        last_run_status (None | str):
        results_final (bool):
        suite_id (UUID):
    """

    last_run_at: datetime.datetime | None
    last_run_id: None | UUID
    last_run_status: None | str
    results_final: bool
    suite_id: UUID
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        last_run_at: None | str
        if isinstance(self.last_run_at, datetime.datetime):
            last_run_at = self.last_run_at.isoformat()
        else:
            last_run_at = self.last_run_at

        last_run_id: None | str
        if isinstance(self.last_run_id, UUID):
            last_run_id = str(self.last_run_id)
        else:
            last_run_id = self.last_run_id

        last_run_status: None | str
        last_run_status = self.last_run_status

        results_final = self.results_final

        suite_id = str(self.suite_id)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "last_run_at": last_run_at,
                "last_run_id": last_run_id,
                "last_run_status": last_run_status,
                "results_final": results_final,
                "suite_id": suite_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)

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

        def _parse_last_run_id(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                last_run_id_type_0 = UUID(data)

                return last_run_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | UUID, data)

        last_run_id = _parse_last_run_id(d.pop("last_run_id"))

        def _parse_last_run_status(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        last_run_status = _parse_last_run_status(d.pop("last_run_status"))

        results_final = d.pop("results_final")

        suite_id = UUID(d.pop("suite_id"))

        suite_compliance_read = cls(
            last_run_at=last_run_at,
            last_run_id=last_run_id,
            last_run_status=last_run_status,
            results_final=results_final,
            suite_id=suite_id,
        )

        suite_compliance_read.additional_properties = d
        return suite_compliance_read

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
