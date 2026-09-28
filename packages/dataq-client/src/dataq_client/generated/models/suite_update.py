from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.suite_target import SuiteTarget


T = TypeVar("T", bound="SuiteUpdate")


@_attrs_define
class SuiteUpdate:
    """
    Attributes:
        description (None | str | Unset):
        name (None | str | Unset):
        target (None | SuiteTarget | Unset):
    """

    description: None | str | Unset = UNSET
    name: None | str | Unset = UNSET
    target: None | SuiteTarget | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.suite_target import SuiteTarget

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        target: dict[str, Any] | None | Unset
        if isinstance(self.target, Unset):
            target = UNSET
        elif isinstance(self.target, SuiteTarget):
            target = self.target.to_dict()
        else:
            target = self.target

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if description is not UNSET:
            field_dict["description"] = description
        if name is not UNSET:
            field_dict["name"] = name
        if target is not UNSET:
            field_dict["target"] = target

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.suite_target import SuiteTarget

        d = dict(src_dict)

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name = _parse_name(d.pop("name", UNSET))

        def _parse_target(data: object) -> None | SuiteTarget | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                target_type_0 = SuiteTarget.from_dict(data)

                return target_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | SuiteTarget | Unset, data)

        target = _parse_target(d.pop("target", UNSET))

        suite_update = cls(
            description=description,
            name=name,
            target=target,
        )

        return suite_update
