from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from typing_extensions import Self

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.suite_target import SuiteTarget


T = TypeVar("T", bound="SuiteCreate")


@_attrs_define
class SuiteCreate:
    """
    Attributes:
        connection_id (UUID):
        name (str):
        description (None | str | Unset):
        tags (list[str] | None | Unset): Replaces the entity's tags. Names are normalized (lower-case, trimmed) and a
            new name creates the tag; `[]` clears them. Omit to leave the tags as they are.
        target (None | SuiteTarget | Unset):
    """

    connection_id: UUID
    name: str
    description: None | str | Unset = UNSET
    tags: list[str] | None | Unset = UNSET
    target: None | SuiteTarget | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.suite_target import SuiteTarget

        connection_id = str(self.connection_id)

        name = self.name

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        tags: list[str] | None | Unset
        if isinstance(self.tags, Unset):
            tags = UNSET
        elif isinstance(self.tags, list):
            tags = self.tags

        else:
            tags = self.tags

        target: dict[str, Any] | None | Unset
        if isinstance(self.target, Unset):
            target = UNSET
        elif isinstance(self.target, SuiteTarget):
            target = self.target.to_dict()
        else:
            target = self.target

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "connection_id": connection_id,
                "name": name,
            }
        )
        if description is not UNSET:
            field_dict["description"] = description
        if tags is not UNSET:
            field_dict["tags"] = tags
        if target is not UNSET:
            field_dict["target"] = target

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.suite_target import SuiteTarget

        d = dict(src_dict)
        connection_id = UUID(d.pop("connection_id"))

        name = d.pop("name")

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_tags(data: object) -> list[str] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                tags_type_0 = cast(list[str], data)

                return tags_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[str] | None | Unset, data)

        tags = _parse_tags(d.pop("tags", UNSET))

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

        suite_create = cls(
            connection_id=connection_id,
            name=name,
            description=description,
            tags=tags,
            target=target,
        )

        return suite_create
