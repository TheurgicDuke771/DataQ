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
    from ..models.check_read_config import CheckReadConfig
    from ..models.check_read_contract_ref_type_0 import CheckReadContractRefType0
    from ..models.inherited_from import InheritedFrom


T = TypeVar("T", bound="CheckRead")


@_attrs_define
class CheckRead:
    """
    Attributes:
        config (CheckReadConfig):
        critical_threshold (float | None):
        engine (str):
        expectation_type (str):
        fail_threshold (float | None):
        id (UUID):
        kind (str):
        name (str):
        suite_id (UUID):
        warn_threshold (float | None):
        alert_snoozed_until (datetime.datetime | None | Unset):
        contract_ref (CheckReadContractRefType0 | None | Unset):
        dimension (None | str | Unset):
        enabled (bool | Unset):  Default: True.
        inherited (InheritedFrom | None | Unset):
        origin (str | Unset):  Default: 'user'.
        source_connection_id (None | Unset | UUID):
        tags (list[str] | Unset):
    """

    config: CheckReadConfig
    critical_threshold: float | None
    engine: str
    expectation_type: str
    fail_threshold: float | None
    id: UUID
    kind: str
    name: str
    suite_id: UUID
    warn_threshold: float | None
    alert_snoozed_until: datetime.datetime | None | Unset = UNSET
    contract_ref: CheckReadContractRefType0 | None | Unset = UNSET
    dimension: None | str | Unset = UNSET
    enabled: bool | Unset = True
    inherited: InheritedFrom | None | Unset = UNSET
    origin: str | Unset = "user"
    source_connection_id: None | Unset | UUID = UNSET
    tags: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.check_read_contract_ref_type_0 import CheckReadContractRefType0
        from ..models.inherited_from import InheritedFrom

        config = self.config.to_dict()

        critical_threshold: float | None
        critical_threshold = self.critical_threshold

        engine = self.engine

        expectation_type = self.expectation_type

        fail_threshold: float | None
        fail_threshold = self.fail_threshold

        id = str(self.id)

        kind = self.kind

        name = self.name

        suite_id = str(self.suite_id)

        warn_threshold: float | None
        warn_threshold = self.warn_threshold

        alert_snoozed_until: None | str | Unset
        if isinstance(self.alert_snoozed_until, Unset):
            alert_snoozed_until = UNSET
        elif isinstance(self.alert_snoozed_until, datetime.datetime):
            alert_snoozed_until = self.alert_snoozed_until.isoformat()
        else:
            alert_snoozed_until = self.alert_snoozed_until

        contract_ref: dict[str, Any] | None | Unset
        if isinstance(self.contract_ref, Unset):
            contract_ref = UNSET
        elif isinstance(self.contract_ref, CheckReadContractRefType0):
            contract_ref = self.contract_ref.to_dict()
        else:
            contract_ref = self.contract_ref

        dimension: None | str | Unset
        if isinstance(self.dimension, Unset):
            dimension = UNSET
        else:
            dimension = self.dimension

        enabled = self.enabled

        inherited: dict[str, Any] | None | Unset
        if isinstance(self.inherited, Unset):
            inherited = UNSET
        elif isinstance(self.inherited, InheritedFrom):
            inherited = self.inherited.to_dict()
        else:
            inherited = self.inherited

        origin = self.origin

        source_connection_id: None | str | Unset
        if isinstance(self.source_connection_id, Unset):
            source_connection_id = UNSET
        elif isinstance(self.source_connection_id, UUID):
            source_connection_id = str(self.source_connection_id)
        else:
            source_connection_id = self.source_connection_id

        tags: list[str] | Unset = UNSET
        if not isinstance(self.tags, Unset):
            tags = self.tags

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "config": config,
                "critical_threshold": critical_threshold,
                "engine": engine,
                "expectation_type": expectation_type,
                "fail_threshold": fail_threshold,
                "id": id,
                "kind": kind,
                "name": name,
                "suite_id": suite_id,
                "warn_threshold": warn_threshold,
            }
        )
        if alert_snoozed_until is not UNSET:
            field_dict["alert_snoozed_until"] = alert_snoozed_until
        if contract_ref is not UNSET:
            field_dict["contract_ref"] = contract_ref
        if dimension is not UNSET:
            field_dict["dimension"] = dimension
        if enabled is not UNSET:
            field_dict["enabled"] = enabled
        if inherited is not UNSET:
            field_dict["inherited"] = inherited
        if origin is not UNSET:
            field_dict["origin"] = origin
        if source_connection_id is not UNSET:
            field_dict["source_connection_id"] = source_connection_id
        if tags is not UNSET:
            field_dict["tags"] = tags

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.check_read_config import CheckReadConfig
        from ..models.check_read_contract_ref_type_0 import (
            CheckReadContractRefType0,
        )
        from ..models.inherited_from import InheritedFrom

        d = dict(src_dict)
        config = CheckReadConfig.from_dict(d.pop("config"))

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

        id = UUID(d.pop("id"))

        kind = d.pop("kind")

        name = d.pop("name")

        suite_id = UUID(d.pop("suite_id"))

        def _parse_warn_threshold(data: object) -> float | None:
            if data is None:
                return data
            return cast(float | None, data)

        warn_threshold = _parse_warn_threshold(d.pop("warn_threshold"))

        def _parse_alert_snoozed_until(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                alert_snoozed_until_type_0 = datetime.datetime.fromisoformat(data)

                return alert_snoozed_until_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        alert_snoozed_until = _parse_alert_snoozed_until(d.pop("alert_snoozed_until", UNSET))

        def _parse_contract_ref(data: object) -> CheckReadContractRefType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                contract_ref_type_0 = CheckReadContractRefType0.from_dict(data)

                return contract_ref_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(CheckReadContractRefType0 | None | Unset, data)

        contract_ref = _parse_contract_ref(d.pop("contract_ref", UNSET))

        def _parse_dimension(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        dimension = _parse_dimension(d.pop("dimension", UNSET))

        enabled = d.pop("enabled", UNSET)

        def _parse_inherited(data: object) -> InheritedFrom | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                inherited_type_0 = InheritedFrom.from_dict(data)

                return inherited_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(InheritedFrom | None | Unset, data)

        inherited = _parse_inherited(d.pop("inherited", UNSET))

        origin = d.pop("origin", UNSET)

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

        tags = cast(list[str], d.pop("tags", UNSET))

        check_read = cls(
            config=config,
            critical_threshold=critical_threshold,
            engine=engine,
            expectation_type=expectation_type,
            fail_threshold=fail_threshold,
            id=id,
            kind=kind,
            name=name,
            suite_id=suite_id,
            warn_threshold=warn_threshold,
            alert_snoozed_until=alert_snoozed_until,
            contract_ref=contract_ref,
            dimension=dimension,
            enabled=enabled,
            inherited=inherited,
            origin=origin,
            source_connection_id=source_connection_id,
            tags=tags,
        )

        check_read.additional_properties = d
        return check_read

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
