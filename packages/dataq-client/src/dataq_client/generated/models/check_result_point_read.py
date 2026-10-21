from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.seasonal_point_read import SeasonalPointRead


T = TypeVar("T", bound="CheckResultPointRead")


@_attrs_define
class CheckResultPointRead:
    """One past result for a check — the per-check trend datum (metric over time).

    Attributes:
        created_at (datetime.datetime):
        metric_value (float | None):
        run_id (UUID):
        status (str):
        seasonal (None | SeasonalPointRead | Unset):
    """

    created_at: datetime.datetime
    metric_value: float | None
    run_id: UUID
    status: str
    seasonal: None | SeasonalPointRead | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.seasonal_point_read import SeasonalPointRead

        created_at = self.created_at.isoformat()

        metric_value: float | None
        metric_value = self.metric_value

        run_id = str(self.run_id)

        status = self.status

        seasonal: dict[str, Any] | None | Unset
        if isinstance(self.seasonal, Unset):
            seasonal = UNSET
        elif isinstance(self.seasonal, SeasonalPointRead):
            seasonal = self.seasonal.to_dict()
        else:
            seasonal = self.seasonal

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "metric_value": metric_value,
                "run_id": run_id,
                "status": status,
            }
        )
        if seasonal is not UNSET:
            field_dict["seasonal"] = seasonal

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.seasonal_point_read import SeasonalPointRead

        d = dict(src_dict)
        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        def _parse_metric_value(data: object) -> float | None:
            if data is None:
                return data
            return cast(float | None, data)

        metric_value = _parse_metric_value(d.pop("metric_value"))

        run_id = UUID(d.pop("run_id"))

        status = d.pop("status")

        def _parse_seasonal(data: object) -> None | SeasonalPointRead | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                seasonal_type_0 = SeasonalPointRead.from_dict(data)

                return seasonal_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | SeasonalPointRead | Unset, data)

        seasonal = _parse_seasonal(d.pop("seasonal", UNSET))

        check_result_point_read = cls(
            created_at=created_at,
            metric_value=metric_value,
            run_id=run_id,
            status=status,
            seasonal=seasonal,
        )

        check_result_point_read.additional_properties = d
        return check_result_point_read

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
