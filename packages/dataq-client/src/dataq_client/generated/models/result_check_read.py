from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

from ..models.result_check_read_resolved_from_type_0 import ResultCheckReadResolvedFromType0
from ..types import UNSET, Unset

T = TypeVar("T", bound="ResultCheckRead")


@_attrs_define
class ResultCheckRead:
    """The check as it WAS when this result was written (#1613) — `engine` (ADR 0036),
    `dimension` (ADR 0038), `expectation_type` and `kind` resolved from `check_versions`
    as of the result's own `created_at`, not from the check's current row, so a check
    re-pointed or re-classified after a run never relabels that run's results. See
    `resolved_from` for the no-history fallback.

        Attributes:
            column (None | str):
            dimension (None | str):
            engine (str):
            expectation_type (None | str):
            kind (None | str):
            name (None | str):
            resolved_from (None | ResultCheckReadResolvedFromType0 | Unset):
    """

    column: None | str
    dimension: None | str
    engine: str
    expectation_type: None | str
    kind: None | str
    name: None | str
    resolved_from: None | ResultCheckReadResolvedFromType0 | Unset = UNSET
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

        resolved_from: None | str | Unset
        if isinstance(self.resolved_from, Unset):
            resolved_from = UNSET
        elif isinstance(self.resolved_from, ResultCheckReadResolvedFromType0):
            resolved_from = self.resolved_from.value
        else:
            resolved_from = self.resolved_from

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
        if resolved_from is not UNSET:
            field_dict["resolved_from"] = resolved_from

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

        def _parse_resolved_from(data: object) -> None | ResultCheckReadResolvedFromType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                resolved_from_type_0 = ResultCheckReadResolvedFromType0(data)

                return resolved_from_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | ResultCheckReadResolvedFromType0 | Unset, data)

        resolved_from = _parse_resolved_from(d.pop("resolved_from", UNSET))

        result_check_read = cls(
            column=column,
            dimension=dimension,
            engine=engine,
            expectation_type=expectation_type,
            kind=kind,
            name=name,
            resolved_from=resolved_from,
        )

        result_check_read.additional_properties = d
        return result_check_read

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
