from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

T = TypeVar("T", bound="ComparisonCheckRead")


@_attrs_define
class ComparisonCheckRead:
    """A check's identity as it WAS when one side's result was written (from
    `check_versions`), never its current state — the two sides of a row may differ.

        Attributes:
            column (None | str):
            dimension (None | str):
            engine (str):
            expectation_type (None | str):
            kind (None | str):
            name (None | str):
    """

    column: None | str
    dimension: None | str
    engine: str
    expectation_type: None | str
    kind: None | str
    name: None | str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        column: None | str
        column = self.column

        dimension: None | str
        dimension = self.dimension

        engine = self.engine

        expectation_type: None | str
        expectation_type = self.expectation_type

        kind: None | str
        kind = self.kind

        name: None | str
        name = self.name

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "column": column,
                "dimension": dimension,
                "engine": engine,
                "expectation_type": expectation_type,
                "kind": kind,
                "name": name,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        d = dict(src_dict)

        def _parse_column(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        column = _parse_column(d.pop("column"))

        def _parse_dimension(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        dimension = _parse_dimension(d.pop("dimension"))

        engine = d.pop("engine")

        def _parse_expectation_type(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        expectation_type = _parse_expectation_type(d.pop("expectation_type"))

        def _parse_kind(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        kind = _parse_kind(d.pop("kind"))

        def _parse_name(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        name = _parse_name(d.pop("name"))

        comparison_check_read = cls(
            column=column,
            dimension=dimension,
            engine=engine,
            expectation_type=expectation_type,
            kind=kind,
            name=name,
        )

        comparison_check_read.additional_properties = d
        return comparison_check_read

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
