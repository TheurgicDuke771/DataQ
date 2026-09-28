from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.check_document_config import CheckDocumentConfig
    from ..models.source_connection_ref import SourceConnectionRef


T = TypeVar("T", bound="CheckDocument")


@_attrs_define
class CheckDocument:
    """One check inside a portable suite document — authoring fields only.

    Attributes:
        expectation_type (str):
        name (str):
        config (CheckDocumentConfig | Unset):
        critical_threshold (None | str | Unset):
        dimension (None | str | Unset):
        engine (str | Unset):  Default: 'gx'.
        fail_threshold (None | str | Unset):
        kind (str | Unset):  Default: 'expectation'.
        source_connection (None | SourceConnectionRef | Unset):
        warn_threshold (None | str | Unset):
    """

    expectation_type: str
    name: str
    config: CheckDocumentConfig | Unset = UNSET
    critical_threshold: None | str | Unset = UNSET
    dimension: None | str | Unset = UNSET
    engine: str | Unset = "gx"
    fail_threshold: None | str | Unset = UNSET
    kind: str | Unset = "expectation"
    source_connection: None | SourceConnectionRef | Unset = UNSET
    warn_threshold: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.source_connection_ref import SourceConnectionRef

        expectation_type = self.expectation_type

        name = self.name

        config: dict[str, Any] | Unset = UNSET
        if not isinstance(self.config, Unset):
            config = self.config.to_dict()

        critical_threshold: None | str | Unset
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

        fail_threshold: None | str | Unset
        if isinstance(self.fail_threshold, Unset):
            fail_threshold = UNSET
        else:
            fail_threshold = self.fail_threshold

        kind = self.kind

        source_connection: dict[str, Any] | None | Unset
        if isinstance(self.source_connection, Unset):
            source_connection = UNSET
        elif isinstance(self.source_connection, SourceConnectionRef):
            source_connection = self.source_connection.to_dict()
        else:
            source_connection = self.source_connection

        warn_threshold: None | str | Unset
        if isinstance(self.warn_threshold, Unset):
            warn_threshold = UNSET
        else:
            warn_threshold = self.warn_threshold

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
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
        if source_connection is not UNSET:
            field_dict["source_connection"] = source_connection
        if warn_threshold is not UNSET:
            field_dict["warn_threshold"] = warn_threshold

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.check_document_config import CheckDocumentConfig
        from ..models.source_connection_ref import SourceConnectionRef

        d = dict(src_dict)
        expectation_type = d.pop("expectation_type")

        name = d.pop("name")

        _config = d.pop("config", UNSET)
        config: CheckDocumentConfig | Unset
        if isinstance(_config, Unset):
            config = UNSET
        else:
            config = CheckDocumentConfig.from_dict(_config)

        def _parse_critical_threshold(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        critical_threshold = _parse_critical_threshold(d.pop("critical_threshold", UNSET))

        def _parse_dimension(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        dimension = _parse_dimension(d.pop("dimension", UNSET))

        engine = d.pop("engine", UNSET)

        def _parse_fail_threshold(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        fail_threshold = _parse_fail_threshold(d.pop("fail_threshold", UNSET))

        kind = d.pop("kind", UNSET)

        def _parse_source_connection(data: object) -> None | SourceConnectionRef | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                source_connection_type_0 = SourceConnectionRef.from_dict(data)

                return source_connection_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | SourceConnectionRef | Unset, data)

        source_connection = _parse_source_connection(d.pop("source_connection", UNSET))

        def _parse_warn_threshold(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        warn_threshold = _parse_warn_threshold(d.pop("warn_threshold", UNSET))

        check_document = cls(
            expectation_type=expectation_type,
            name=name,
            config=config,
            critical_threshold=critical_threshold,
            dimension=dimension,
            engine=engine,
            fail_threshold=fail_threshold,
            kind=kind,
            source_connection=source_connection,
            warn_threshold=warn_threshold,
        )

        check_document.additional_properties = d
        return check_document

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
