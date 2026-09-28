from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.check_update_config_type_0 import CheckUpdateConfigType0


T = TypeVar("T", bound="CheckUpdate")


@_attrs_define
class CheckUpdate:
    """
    Attributes:
        config (CheckUpdateConfigType0 | None | Unset):
        critical_threshold (float | None | str | Unset):
        dimension (None | str | Unset):
        engine (None | str | Unset):
        expectation_type (None | str | Unset):
        fail_threshold (float | None | str | Unset):
        name (None | str | Unset):
        source_connection_id (None | Unset | UUID):
        warn_threshold (float | None | str | Unset):
    """

    config: CheckUpdateConfigType0 | None | Unset = UNSET
    critical_threshold: float | None | str | Unset = UNSET
    dimension: None | str | Unset = UNSET
    engine: None | str | Unset = UNSET
    expectation_type: None | str | Unset = UNSET
    fail_threshold: float | None | str | Unset = UNSET
    name: None | str | Unset = UNSET
    source_connection_id: None | Unset | UUID = UNSET
    warn_threshold: float | None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.check_update_config_type_0 import CheckUpdateConfigType0

        config: dict[str, Any] | None | Unset
        if isinstance(self.config, Unset):
            config = UNSET
        elif isinstance(self.config, CheckUpdateConfigType0):
            config = self.config.to_dict()
        else:
            config = self.config

        critical_threshold: float | None | str | Unset
        if isinstance(self.critical_threshold, Unset):
            critical_threshold = UNSET
        else:
            critical_threshold = self.critical_threshold

        dimension: None | str | Unset
        if isinstance(self.dimension, Unset):
            dimension = UNSET
        else:
            dimension = self.dimension

        engine: None | str | Unset
        if isinstance(self.engine, Unset):
            engine = UNSET
        else:
            engine = self.engine

        expectation_type: None | str | Unset
        if isinstance(self.expectation_type, Unset):
            expectation_type = UNSET
        else:
            expectation_type = self.expectation_type

        fail_threshold: float | None | str | Unset
        if isinstance(self.fail_threshold, Unset):
            fail_threshold = UNSET
        else:
            fail_threshold = self.fail_threshold

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        source_connection_id: None | str | Unset
        if isinstance(self.source_connection_id, Unset):
            source_connection_id = UNSET
        elif isinstance(self.source_connection_id, UUID):
            source_connection_id = str(self.source_connection_id)
        else:
            source_connection_id = self.source_connection_id

        warn_threshold: float | None | str | Unset
        if isinstance(self.warn_threshold, Unset):
            warn_threshold = UNSET
        else:
            warn_threshold = self.warn_threshold

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if config is not UNSET:
            field_dict["config"] = config
        if critical_threshold is not UNSET:
            field_dict["critical_threshold"] = critical_threshold
        if dimension is not UNSET:
            field_dict["dimension"] = dimension
        if engine is not UNSET:
            field_dict["engine"] = engine
        if expectation_type is not UNSET:
            field_dict["expectation_type"] = expectation_type
        if fail_threshold is not UNSET:
            field_dict["fail_threshold"] = fail_threshold
        if name is not UNSET:
            field_dict["name"] = name
        if source_connection_id is not UNSET:
            field_dict["source_connection_id"] = source_connection_id
        if warn_threshold is not UNSET:
            field_dict["warn_threshold"] = warn_threshold

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.check_update_config_type_0 import CheckUpdateConfigType0

        d = dict(src_dict)

        def _parse_config(data: object) -> CheckUpdateConfigType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                config_type_0 = CheckUpdateConfigType0.from_dict(data)

                return config_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(CheckUpdateConfigType0 | None | Unset, data)

        config = _parse_config(d.pop("config", UNSET))

        def _parse_critical_threshold(data: object) -> float | None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | str | Unset, data)

        critical_threshold = _parse_critical_threshold(d.pop("critical_threshold", UNSET))

        def _parse_dimension(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        dimension = _parse_dimension(d.pop("dimension", UNSET))

        def _parse_engine(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        engine = _parse_engine(d.pop("engine", UNSET))

        def _parse_expectation_type(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        expectation_type = _parse_expectation_type(d.pop("expectation_type", UNSET))

        def _parse_fail_threshold(data: object) -> float | None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | str | Unset, data)

        fail_threshold = _parse_fail_threshold(d.pop("fail_threshold", UNSET))

        def _parse_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name = _parse_name(d.pop("name", UNSET))

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

        def _parse_warn_threshold(data: object) -> float | None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | str | Unset, data)

        warn_threshold = _parse_warn_threshold(d.pop("warn_threshold", UNSET))

        check_update = cls(
            config=config,
            critical_threshold=critical_threshold,
            dimension=dimension,
            engine=engine,
            expectation_type=expectation_type,
            fail_threshold=fail_threshold,
            name=name,
            source_connection_id=source_connection_id,
            warn_threshold=warn_threshold,
        )

        return check_update
