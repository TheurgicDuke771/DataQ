from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

T = TypeVar("T", bound="BulkThresholdsRequest")


@_attrs_define
class BulkThresholdsRequest:
    """Like `CheckUpdate`: an explicit `null` clears that threshold on every check, and
    leaving the key out keeps each check's own value.

        Attributes:
            check_ids (list[UUID]):
            critical_threshold (float | None | str | Unset):
            fail_threshold (float | None | str | Unset):
            warn_threshold (float | None | str | Unset):
    """

    check_ids: list[UUID]
    critical_threshold: float | None | str | Unset = UNSET
    fail_threshold: float | None | str | Unset = UNSET
    warn_threshold: float | None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        check_ids = []
        for check_ids_item_data in self.check_ids:
            check_ids_item = str(check_ids_item_data)
            check_ids.append(check_ids_item)

        critical_threshold: float | None | str | Unset
        if isinstance(self.critical_threshold, Unset):
            critical_threshold = UNSET
        else:
            critical_threshold = self.critical_threshold

        fail_threshold: float | None | str | Unset
        if isinstance(self.fail_threshold, Unset):
            fail_threshold = UNSET
        else:
            fail_threshold = self.fail_threshold

        warn_threshold: float | None | str | Unset
        if isinstance(self.warn_threshold, Unset):
            warn_threshold = UNSET
        else:
            warn_threshold = self.warn_threshold

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "check_ids": check_ids,
            }
        )
        if critical_threshold is not UNSET:
            field_dict["critical_threshold"] = critical_threshold
        if fail_threshold is not UNSET:
            field_dict["fail_threshold"] = fail_threshold
        if warn_threshold is not UNSET:
            field_dict["warn_threshold"] = warn_threshold

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        check_ids = []
        _check_ids = d.pop("check_ids")
        for check_ids_item_data in _check_ids:
            check_ids_item = UUID(check_ids_item_data)

            check_ids.append(check_ids_item)

        def _parse_critical_threshold(data: object) -> float | None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | str | Unset, data)

        critical_threshold = _parse_critical_threshold(d.pop("critical_threshold", UNSET))

        def _parse_fail_threshold(data: object) -> float | None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | str | Unset, data)

        fail_threshold = _parse_fail_threshold(d.pop("fail_threshold", UNSET))

        def _parse_warn_threshold(data: object) -> float | None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | str | Unset, data)

        warn_threshold = _parse_warn_threshold(d.pop("warn_threshold", UNSET))

        bulk_thresholds_request = cls(
            check_ids=check_ids,
            critical_threshold=critical_threshold,
            fail_threshold=fail_threshold,
            warn_threshold=warn_threshold,
        )

        return bulk_thresholds_request
