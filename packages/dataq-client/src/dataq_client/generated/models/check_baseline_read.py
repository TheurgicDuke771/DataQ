from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.check_baseline_read_baseline import CheckBaselineReadBaseline


T = TypeVar("T", bound="CheckBaselineRead")


@_attrs_define
class CheckBaselineRead:
    """A stateful monitor's stored baseline — `schema_drift`'s column snapshot or `anomaly`'s
    observation window (`monitor_baselines.baseline`, kind-shaped JSONB; see
    `backend/app/services/anomaly.py` for the payload's documented shape). Returned generically:
    this endpoint doesn't interpret `baseline`, the frontend trend chart does, keyed on `kind`.

        Attributes:
            baseline (CheckBaselineReadBaseline):
            captured_at (datetime.datetime):
            kind (str):
    """

    baseline: CheckBaselineReadBaseline
    captured_at: datetime.datetime
    kind: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        baseline = self.baseline.to_dict()

        captured_at = self.captured_at.isoformat()

        kind = self.kind

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "baseline": baseline,
                "captured_at": captured_at,
                "kind": kind,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.check_baseline_read_baseline import CheckBaselineReadBaseline

        d = dict(src_dict)
        baseline = CheckBaselineReadBaseline.from_dict(d.pop("baseline"))

        captured_at = datetime.datetime.fromisoformat(d.pop("captured_at"))

        kind = d.pop("kind")

        check_baseline_read = cls(
            baseline=baseline,
            captured_at=captured_at,
            kind=kind,
        )

        check_baseline_read.additional_properties = d
        return check_baseline_read

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
