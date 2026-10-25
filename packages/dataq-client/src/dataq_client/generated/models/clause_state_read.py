from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from uuid import UUID

from attrs import define as _attrs_define
from attrs import field as _attrs_field
from typing_extensions import Self

if TYPE_CHECKING:
    from ..models.clause_state_read_clause import ClauseStateReadClause


T = TypeVar("T", bound="ClauseStateRead")


@_attrs_define
class ClauseStateRead:
    """
    Attributes:
        check_id (None | UUID):
        check_name (None | str):
        clause (ClauseStateReadClause):
        drifted (bool | None):
        state (str):
        suite_id (UUID):
    """

    check_id: None | UUID
    check_name: None | str
    clause: ClauseStateReadClause
    drifted: bool | None
    state: str
    suite_id: UUID
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        check_id: None | str
        if isinstance(self.check_id, UUID):
            check_id = str(self.check_id)
        else:
            check_id = self.check_id

        check_name: None | str
        check_name = self.check_name

        clause = self.clause.to_dict()

        drifted: bool | None
        drifted = self.drifted

        state = self.state

        suite_id = str(self.suite_id)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "check_id": check_id,
                "check_name": check_name,
                "clause": clause,
                "drifted": drifted,
                "state": state,
                "suite_id": suite_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls, src_dict: Mapping[str, Any]) -> Self:
        from ..models.clause_state_read_clause import ClauseStateReadClause

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

        def _parse_check_name(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        check_name = _parse_check_name(d.pop("check_name"))

        clause = ClauseStateReadClause.from_dict(d.pop("clause"))

        def _parse_drifted(data: object) -> bool | None:
            if data is None:
                return data
            return cast(bool | None, data)

        drifted = _parse_drifted(d.pop("drifted"))

        state = d.pop("state")

        suite_id = UUID(d.pop("suite_id"))

        clause_state_read = cls(
            check_id=check_id,
            check_name=check_name,
            clause=clause,
            drifted=drifted,
            state=state,
            suite_id=suite_id,
        )

        clause_state_read.additional_properties = d
        return clause_state_read

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
