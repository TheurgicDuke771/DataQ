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
    from ..models.check_version_read_config import CheckVersionReadConfig


T = TypeVar("T", bound="CheckVersionRead")


@_attrs_define
class CheckVersionRead:
    """One snapshot in a check's history. Like `CheckRead`, thresholds are coerced
    Decimal→float by Pydantic; `changed_by_name` (the author's display name or
    email, NULL for a system actor / removed user) comes from the model property,
    resolved server-side so the drawer needn't join users.

        Attributes:
            changed_by (None | UUID):
            changed_by_name (None | str):
            config (CheckVersionReadConfig):
            created_at (datetime.datetime):
            critical_threshold (float | None):
            engine (str):
            expectation_type (str):
            fail_threshold (float | None):
            kind (str):
            name (str):
            version_no (int):
            warn_threshold (float | None):
            dimension (None | str | Unset):
            enabled (bool | Unset):  Default: True.
            source_connection_id (None | Unset | UUID):
    """

    changed_by: None | UUID
    changed_by_name: None | str
    config: CheckVersionReadConfig
    created_at: datetime.datetime
    critical_threshold: float | None
    engine: str
    expectation_type: str
    fail_threshold: float | None
    kind: str
    name: str
    version_no: int
    warn_threshold: float | None
    dimension: None | str | Unset = UNSET
    enabled: bool | Unset = True
    source_connection_id: None | Unset | UUID = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        changed_by: None | str
        if isinstance(self.changed_by, UUID):
            changed_by = str(self.changed_by)
        else:
            changed_by = self.changed_by

        changed_by_name: None | str
        changed_by_name = self.changed_by_name

        config = self.config.to_dict()

        created_at = self.created_at.isoformat()

        critical_threshold: float | None
        critical_threshold = self.critical_threshold

        engine = self.engine

        expectation_type = self.expectation_type

        fail_threshold: float | None
        fail_threshold = self.fail_threshold

        kind = self.kind

        name = self.name

        version_no = self.version_no

        warn_threshold: float | None
        warn_threshold = self.warn_threshold

        dimension: None | str | Unset
        if isinstance(self.dimension, Unset):
            dimension = UNSET
        else:
            dimension = self.dimension

        enabled = self.enabled

        source_connection_id: None | str | Unset
        if isinstance(self.source_connection_id, Unset):
            source_connection_id = UNSET
        elif isinstance(self.source_connection_id, UUID):
            source_connection_id = str(self.source_connection_id)
        else:
            source_connection_id = self.source_connection_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "changed_by": changed_by,
                "changed_by_name": changed_by_name,
                "config": config,
                "created_at": created_at,
                "critical_threshold": critical_threshold,
                "engine": engine,
                "expectation_type": expectation_type,
                "fail_threshold": fail_threshold,
                "kind": kind,
                "name": name,
                "version_no": version_no,
                "warn_threshold": warn_threshold,
            }
        )
        if dimension is not UNSET:
            field_dict["dimension"] = dimension
        if enabled is not UNSET:
            field_dict["enabled"] = enabled
        if source_connection_id is not UNSET:
            field_dict["source_connection_id"] = source_connection_id

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.check_version_read_config import CheckVersionReadConfig

        d = dict(src_dict)

        def _parse_changed_by(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                changed_by_type_0 = UUID(data)

                return changed_by_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | UUID, data)

        changed_by = _parse_changed_by(d.pop("changed_by"))

        def _parse_changed_by_name(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        changed_by_name = _parse_changed_by_name(d.pop("changed_by_name"))

        config = CheckVersionReadConfig.from_dict(d.pop("config"))

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        def _parse_critical_threshold(data: object) -> float | None:
            if data is None:
                return data
            return cast(float | None, data)

        critical_threshold = _parse_critical_threshold(d.pop("critical_threshold"))

        engine = d.pop("engine")

        expectation_type = d.pop("expectation_type")

        def _parse_fail_threshold(data: object) -> float | None:
            if data is None:
                return data
            return cast(float | None, data)

        fail_threshold = _parse_fail_threshold(d.pop("fail_threshold"))

        kind = d.pop("kind")

        name = d.pop("name")

        version_no = d.pop("version_no")

        def _parse_warn_threshold(data: object) -> float | None:
            if data is None:
                return data
            return cast(float | None, data)

        warn_threshold = _parse_warn_threshold(d.pop("warn_threshold"))

        def _parse_dimension(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        dimension = _parse_dimension(d.pop("dimension", UNSET))

        enabled = d.pop("enabled", UNSET)

        def _parse_source_connection_id(data: object) -> None | Unset | UUID:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                source_connection_id_type_0 = UUID(data)

                return source_connection_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | Unset | UUID, data)

        source_connection_id = _parse_source_connection_id(d.pop("source_connection_id", UNSET))

        check_version_read = cls(
            changed_by=changed_by,
            changed_by_name=changed_by_name,
            config=config,
            created_at=created_at,
            critical_threshold=critical_threshold,
            engine=engine,
            expectation_type=expectation_type,
            fail_threshold=fail_threshold,
            kind=kind,
            name=name,
            version_no=version_no,
            warn_threshold=warn_threshold,
            dimension=dimension,
            enabled=enabled,
            source_connection_id=source_connection_id,
        )

        check_version_read.additional_properties = d
        return check_version_read

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
