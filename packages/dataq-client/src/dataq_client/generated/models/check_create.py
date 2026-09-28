from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.check_create_config import CheckCreateConfig


T = TypeVar("T", bound="CheckCreate")


@_attrs_define
class CheckCreate:
    """
    Attributes:
        expectation_type (str):
        name (str):
        config (CheckCreateConfig | Unset):
        critical_threshold (float | None | str | Unset):
        dimension (None | str | Unset):
        engine (str | Unset):  Default: 'gx'.
        fail_threshold (float | None | str | Unset):
        kind (str | Unset):  Default: 'expectation'.
        source_connection_id (None | Unset | UUID):
        warn_threshold (float | None | str | Unset):
    """

    expectation_type: str
    name: str
    config: CheckCreateConfig | Unset = UNSET
    critical_threshold: float | None | str | Unset = UNSET
    dimension: None | str | Unset = UNSET
    engine: str | Unset = "gx"
    fail_threshold: float | None | str | Unset = UNSET
    kind: str | Unset = "expectation"
    source_connection_id: None | Unset | UUID = UNSET
    warn_threshold: float | None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        expectation_type = self.expectation_type

        name = self.name

        config: dict[str, Any] | Unset = UNSET
        if not isinstance(self.config, Unset):
            config = self.config.to_dict()

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

        engine = self.engine

        fail_threshold: float | None | str | Unset
        if isinstance(self.fail_threshold, Unset):
            fail_threshold = UNSET
        else:
            fail_threshold = self.fail_threshold

        kind = self.kind

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

        field_dict.update(
            {
                "expectation_type": expectation_type,
                "name": name,
            }
        )
        if config is not UNSET:
            field_dict["config"] = config
        if critical_threshold is not UNSET:
            field_dict["critical_threshold"] = critical_threshold
        if dimension is not UNSET:
            field_dict["dimension"] = dimension
        if engine is not UNSET:
            field_dict["engine"] = engine
        if fail_threshold is not UNSET:
            field_dict["fail_threshold"] = fail_threshold
        if kind is not UNSET:
            field_dict["kind"] = kind
        if source_connection_id is not UNSET:
            field_dict["source_connection_id"] = source_connection_id
        if warn_threshold is not UNSET:
            field_dict["warn_threshold"] = warn_threshold

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.check_create_config import CheckCreateConfig

        d = dict(src_dict)
        expectation_type = d.pop("expectation_type")

        name = d.pop("name")

        _config = d.pop("config", UNSET)
        config: CheckCreateConfig | Unset
        if isinstance(_config, Unset):
            config = UNSET
        else:
            config = CheckCreateConfig.from_dict(_config)

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

        engine = d.pop("engine", UNSET)

        def _parse_fail_threshold(data: object) -> float | None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | str | Unset, data)

        fail_threshold = _parse_fail_threshold(d.pop("fail_threshold", UNSET))

        kind = d.pop("kind", UNSET)

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

        check_create = cls(
            expectation_type=expectation_type,
            name=name,
            config=config,
            critical_threshold=critical_threshold,
            dimension=dimension,
            engine=engine,
            fail_threshold=fail_threshold,
            kind=kind,
            source_connection_id=source_connection_id,
            warn_threshold=warn_threshold,
        )

        return check_create
