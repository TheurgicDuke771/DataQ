from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.secret_sweep_report_read_mode_type_0 import SecretSweepReportReadModeType0
from ..models.secret_sweep_report_read_status import SecretSweepReportReadStatus
from ..types import UNSET, Unset

T = TypeVar("T", bound="SecretSweepReportRead")


@_attrs_define
class SecretSweepReportRead:
    """The last orphan-secret sweep run, beat OR manual. `status="never_run"` means
    the sweep has never recorded a report — never read that as `orphan_count=0`;
    `status="skipped"` means a run happened but never actually enumerated the store
    (grace disabled, or the store cannot list its own secrets) — also never `0`.
    `orphan_count` is `None` (never `0`) on either a skip or a store outage; `error`
    then carries the classified, secret-free reason. `scanned`/`unknown_age_count`/
    `too_young_count` are the store-side total and the two other unowned buckets a
    completed run split secrets into — also `None` on a skip/outage, so N secrets the
    store couldn't date (often an OpenBao token missing metadata `read`) doesn't read
    as a clean vault. `orphan_names` are secret NAMES only, capped at 200 — see
    `truncated`.

        Attributes:
            status (SecretSweepReportReadStatus):
            error (None | str | Unset):
            mode (None | SecretSweepReportReadModeType0 | Unset):
            orphan_count (int | None | Unset):
            orphan_names (list[str] | Unset):
            ran_at (datetime.datetime | None | Unset):
            scanned (int | None | Unset):
            store (None | str | Unset):
            too_young_count (int | None | Unset):
            truncated (bool | Unset):  Default: False.
            unknown_age_count (int | None | Unset):
    """

    status: SecretSweepReportReadStatus
    error: None | str | Unset = UNSET
    mode: None | SecretSweepReportReadModeType0 | Unset = UNSET
    orphan_count: int | None | Unset = UNSET
    orphan_names: list[str] | Unset = UNSET
    ran_at: datetime.datetime | None | Unset = UNSET
    scanned: int | None | Unset = UNSET
    store: None | str | Unset = UNSET
    too_young_count: int | None | Unset = UNSET
    truncated: bool | Unset = False
    unknown_age_count: int | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        status = self.status.value

        error: None | str | Unset
        if isinstance(self.error, Unset):
            error = UNSET
        else:
            error = self.error

        mode: None | str | Unset
        if isinstance(self.mode, Unset):
            mode = UNSET
        elif isinstance(self.mode, SecretSweepReportReadModeType0):
            mode = self.mode.value
        else:
            mode = self.mode

        orphan_count: int | None | Unset
        if isinstance(self.orphan_count, Unset):
            orphan_count = UNSET
        else:
            orphan_count = self.orphan_count

        orphan_names: list[str] | Unset = UNSET
        if not isinstance(self.orphan_names, Unset):
            orphan_names = self.orphan_names

        ran_at: None | str | Unset
        if isinstance(self.ran_at, Unset):
            ran_at = UNSET
        elif isinstance(self.ran_at, datetime.datetime):
            ran_at = self.ran_at.isoformat()
        else:
            ran_at = self.ran_at

        scanned: int | None | Unset
        if isinstance(self.scanned, Unset):
            scanned = UNSET
        else:
            scanned = self.scanned

        store: None | str | Unset
        if isinstance(self.store, Unset):
            store = UNSET
        else:
            store = self.store

        too_young_count: int | None | Unset
        if isinstance(self.too_young_count, Unset):
            too_young_count = UNSET
        else:
            too_young_count = self.too_young_count

        truncated = self.truncated

        unknown_age_count: int | None | Unset
        if isinstance(self.unknown_age_count, Unset):
            unknown_age_count = UNSET
        else:
            unknown_age_count = self.unknown_age_count

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "status": status,
            }
        )
        if error is not UNSET:
            field_dict["error"] = error
        if mode is not UNSET:
            field_dict["mode"] = mode
        if orphan_count is not UNSET:
            field_dict["orphan_count"] = orphan_count
        if orphan_names is not UNSET:
            field_dict["orphan_names"] = orphan_names
        if ran_at is not UNSET:
            field_dict["ran_at"] = ran_at
        if scanned is not UNSET:
            field_dict["scanned"] = scanned
        if store is not UNSET:
            field_dict["store"] = store
        if too_young_count is not UNSET:
            field_dict["too_young_count"] = too_young_count
        if truncated is not UNSET:
            field_dict["truncated"] = truncated
        if unknown_age_count is not UNSET:
            field_dict["unknown_age_count"] = unknown_age_count

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)
        status = SecretSweepReportReadStatus(d.pop("status"))

        def _parse_error(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        error = _parse_error(d.pop("error", UNSET))

        def _parse_mode(data: object) -> None | SecretSweepReportReadModeType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                mode_type_0 = SecretSweepReportReadModeType0(data)

                return mode_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | SecretSweepReportReadModeType0 | Unset, data)

        mode = _parse_mode(d.pop("mode", UNSET))

        def _parse_orphan_count(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        orphan_count = _parse_orphan_count(d.pop("orphan_count", UNSET))

        orphan_names = cast(list[str], d.pop("orphan_names", UNSET))

        def _parse_ran_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                ran_at_type_0 = datetime.datetime.fromisoformat(data)

                return ran_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        ran_at = _parse_ran_at(d.pop("ran_at", UNSET))

        def _parse_scanned(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        scanned = _parse_scanned(d.pop("scanned", UNSET))

        def _parse_store(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        store = _parse_store(d.pop("store", UNSET))

        def _parse_too_young_count(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        too_young_count = _parse_too_young_count(d.pop("too_young_count", UNSET))

        truncated = d.pop("truncated", UNSET)

        def _parse_unknown_age_count(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        unknown_age_count = _parse_unknown_age_count(d.pop("unknown_age_count", UNSET))

        secret_sweep_report_read = cls(
            status=status,
            error=error,
            mode=mode,
            orphan_count=orphan_count,
            orphan_names=orphan_names,
            ran_at=ran_at,
            scanned=scanned,
            store=store,
            too_young_count=too_young_count,
            truncated=truncated,
            unknown_age_count=unknown_age_count,
        )

        secret_sweep_report_read.additional_properties = d
        return secret_sweep_report_read

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
