from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.suggestion_read_config import SuggestionReadConfig


T = TypeVar("T", bound="SuggestionRead")


@_attrs_define
class SuggestionRead:
    """
    Attributes:
        check_id (None | UUID):
        config (SuggestionReadConfig):
        created_at (datetime.datetime):
        decided_at (datetime.datetime | None):
        decided_by (None | UUID):
        expectation_type (str):
        id (UUID):
        name (str):
        rationale (None | str):
        source (str):
        status (str):
        suite_id (UUID):
    """

    check_id: None | UUID
    config: SuggestionReadConfig
    created_at: datetime.datetime
    decided_at: datetime.datetime | None
    decided_by: None | UUID
    expectation_type: str
    id: UUID
    name: str
    rationale: None | str
    source: str
    status: str
    suite_id: UUID
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        check_id: None | str
        if isinstance(self.check_id, UUID):
            check_id = str(self.check_id)
        else:
            check_id = self.check_id

        config = self.config.to_dict()

        created_at = self.created_at.isoformat()

        decided_at: None | str
        if isinstance(self.decided_at, datetime.datetime):
            decided_at = self.decided_at.isoformat()
        else:
            decided_at = self.decided_at

        decided_by: None | str
        if isinstance(self.decided_by, UUID):
            decided_by = str(self.decided_by)
        else:
            decided_by = self.decided_by

        expectation_type = self.expectation_type

        id = str(self.id)

        name = self.name

        rationale: None | str
        rationale = self.rationale

        source = self.source

        status = self.status

        suite_id = str(self.suite_id)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "check_id": check_id,
                "config": config,
                "created_at": created_at,
                "decided_at": decided_at,
                "decided_by": decided_by,
                "expectation_type": expectation_type,
                "id": id,
                "name": name,
                "rationale": rationale,
                "source": source,
                "status": status,
                "suite_id": suite_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.suggestion_read_config import SuggestionReadConfig

        d = dict(src_dict)

        def _parse_check_id(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                check_id_type_0 = UUID(data)

                return check_id_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | UUID, data)

        check_id = _parse_check_id(d.pop("check_id"))

        config = SuggestionReadConfig.from_dict(d.pop("config"))

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        def _parse_decided_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                decided_at_type_0 = datetime.datetime.fromisoformat(data)

                return decided_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        decided_at = _parse_decided_at(d.pop("decided_at"))

        def _parse_decided_by(data: object) -> None | UUID:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                decided_by_type_0 = UUID(data)

                return decided_by_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | UUID, data)

        decided_by = _parse_decided_by(d.pop("decided_by"))

        expectation_type = d.pop("expectation_type")

        id = UUID(d.pop("id"))

        name = d.pop("name")

        def _parse_rationale(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        rationale = _parse_rationale(d.pop("rationale"))

        source = d.pop("source")

        status = d.pop("status")

        suite_id = UUID(d.pop("suite_id"))

        suggestion_read = cls(
            check_id=check_id,
            config=config,
            created_at=created_at,
            decided_at=decided_at,
            decided_by=decided_by,
            expectation_type=expectation_type,
            id=id,
            name=name,
            rationale=rationale,
            source=source,
            status=status,
            suite_id=suite_id,
        )

        suggestion_read.additional_properties = d
        return suggestion_read

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
